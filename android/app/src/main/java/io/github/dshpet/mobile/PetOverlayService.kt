package io.github.dshpet.mobile

import android.app.AppOpsManager
import android.app.KeyguardManager
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.content.SharedPreferences
import android.content.pm.ServiceInfo
import android.content.res.Configuration
import android.graphics.Color
import android.graphics.PixelFormat
import android.graphics.Rect
import android.graphics.drawable.GradientDrawable
import android.os.Build
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.provider.Settings
import android.util.DisplayMetrics
import android.util.Log
import android.view.Choreographer
import android.view.Gravity
import android.view.KeyEvent
import android.view.MotionEvent
import android.view.View
import android.view.WindowInsets
import android.view.WindowManager
import android.widget.Button
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import io.github.dshpet.mobile.modules.ClickSoundModule
import io.github.dshpet.mobile.modules.CollisionModule
import io.github.dshpet.mobile.modules.CompanionModule
import io.github.dshpet.mobile.modules.DshLinkModule
import io.github.dshpet.mobile.modules.ModuleFactory
import io.github.dshpet.mobile.modules.ModuleHost
import io.github.dshpet.mobile.modules.ModuleRegistry
import io.github.dshpet.mobile.modules.PetEvent
import io.github.dshpet.mobile.modules.SpeechModule
import io.github.dshpet.mobile.modules.WanderModule
import java.io.FileDescriptor
import java.io.PrintWriter
import kotlin.math.max
import kotlin.math.roundToInt

/**
 * The single in-process overlay host. It owns the pets (0 is the mother, 1..4
 * are baby whales), their windows, the shared decoder and the module registry;
 * optional behaviour lives in modules that are only built while enabled.
 */
class PetOverlayService : Service(), Choreographer.FrameCallback, ModuleHost, PetView.Listener {
    companion object {
        const val START = "pet.START"
        const val PAUSE = "pet.PAUSE"
        const val RESUME = "pet.RESUME"
        const val STOP = "pet.STOP"
        const val RESET = "pet.RESET"
        const val SPAWN = "pet.SPAWN"
        const val EXIT = "pet.EXIT"
        private const val CHANNEL = "pet-running"
        private const val TAG = "PetOverlay"

        /** About 26 decoded frames; the cache is measured in bytes, not entries. */
        private const val FRAME_BUDGET_BYTES = 24L * 1024 * 1024

        /** Frames decoded ahead of every pet's play head. */
        private const val LOOKAHEAD = 4

        /** First frames of each tap animation, decoded while nothing is happening. */
        private const val WARM_FRAMES = 2

        /** One decode loop keeps a full house of five pets inside its budget. */
        private const val DECODE_THREADS = 1

        fun command(context: Context, action: String) {
            try {
                val intent = Intent(context, PetOverlayService::class.java).setAction(action)
                when (action) {
                    STOP -> context.stopService(intent)
                    START -> ContextCompat.startForegroundService(context, intent)
                    else -> context.startService(intent)
                }
            } catch (e: RuntimeException) {
                Log.e(TAG, "Service command failed", e)
                PetRuntime.state.value = PetRuntime.state.value.copy(error = context.getString(R.string.start_failed))
            }
        }

        private val MODULE_FACTORIES = listOf(
            ModuleFactory(ModuleIds.WANDER) { WanderModule() },
            ModuleFactory(ModuleIds.SPEECH) { SpeechModule() },
            ModuleFactory(ModuleIds.CLICK_SOUND) { ClickSoundModule() },
            ModuleFactory(ModuleIds.COMPANION) { CompanionModule() },
            ModuleFactory(ModuleIds.COLLISION) { CollisionModule() },
            // 电脑端 DSH 联动：默认关，关闭时这个工厂不会被调用（无线程/无 socket）。
            ModuleFactory(ModuleIds.DSH_LINK) { DshLinkModule() },
        )
    }

    /** Android-side attachment of a pet: window, view, bubble, decoder cursor. */
    private class Surface(val handle: PetHandle) {
        var view: PetView? = null
        var params: WindowManager.LayoutParams? = null
        var cursor: FrameLoader.Cursor? = null
        var bubble: TextView? = null
        var scale = 1.0
        var dismissBubble: Runnable? = null
    }

    private val handler = Handler(Looper.getMainLooper())
    private lateinit var windows: WindowManager
    private lateinit var preferences: PetPreferences
    private lateinit var catalog: AssetCatalog
    private var loader: FrameLoader? = null
    private var registry: ModuleRegistry? = null
    private var settings = PetOptions()
    private val petMap = LinkedHashMap<Int, PetHandle>()
    private val surfaces = LinkedHashMap<Int, Surface>()
    private var motherScale = 1.0
    private var safe = Rect()
    private var started = false
    private var registered = false
    private var userPaused = false
    private var frameScheduled = false
    private var lastFrameNanos = 0L
    private var actions: View? = null
    private var actionsPetId = -1
    private var decodedTotal = 0L
    private var reusedTotal = 0L
    private var hitsTotal = 0L
    private var missesTotal = 0L
    private val preferenceListener = SharedPreferences.OnSharedPreferenceChangeListener { _, key ->
        if (key == null || key in PetPreferences.WATCHED_KEYS) handler.post { reloadOptions() }
    }
    private val permissionListener = AppOpsManager.OnOpChangedListener { op, pkg ->
        if (op == AppOpsManager.OPSTR_SYSTEM_ALERT_WINDOW && pkg == packageName) handler.post {
            if (started && !Settings.canDrawOverlays(this)) fail(R.string.permission_required)
        }
    }
    private val screenReceiver = object : BroadcastReceiver() {
        override fun onReceive(context: Context, intent: Intent) { updateVisibility() }
    }

    override fun onCreate() {
        super.onCreate()
        windows = getSystemService(WindowManager::class.java)
        preferences = PetPreferences(this)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            START -> startPet()
            STOP -> stopSelf()
            PAUSE -> if (started) { userPaused = true; updateVisibility(); updateNotification() }
            RESUME -> if (started) { userPaused = false; updateVisibility(); updateNotification() }
            RESET -> if (started) {
                petMap[0]?.engine?.restorePosition(.7, 1.0)
                exitAllCompanions()
                reloadOptions()
            }
            SPAWN -> if (started) spawnCompanion()
            EXIT -> if (started) petMap[0]?.let { exitCompanion(it) }
            else -> if (!started) stopSelf()
        }
        return START_NOT_STICKY
    }

    private fun startPet() {
        if (started) return
        if (!Settings.canDrawOverlays(this)) { fail(R.string.permission_required); return }
        try {
            val manager = getSystemService(NotificationManager::class.java)
            manager.createNotificationChannel(NotificationChannel(CHANNEL,
                getString(R.string.channel_name), NotificationManager.IMPORTANCE_LOW))
            val type = if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE else 0
            ServiceCompat.startForeground(this, 1, notification(), type)
            catalog = AssetCatalog.load(assets)
            settings = preferences.options()
            safe = safeBounds()
            motherScale = scaleFor(settings.size)
            // Pets and their positions come back first: every module sees the
            // full household when it is enabled below.
            val mother = PetHandle(0, PetEngine(catalog.clips))
            mother.engine.wander = settings.wander
            resizeEngine(mother.engine, motherScale)
            val saved = preferences.position()
            mother.engine.restorePosition(saved.first, saved.second)
            petMap[0] = mother
            started = true
            restoreCompanions()
            registry = ModuleRegistry(this, MODULE_FACTORIES).also { it.apply() }
            preferences.subscribe(preferenceListener)
            ContextCompat.registerReceiver(this, screenReceiver, IntentFilter().apply {
                addAction(Intent.ACTION_SCREEN_OFF)
                addAction(Intent.ACTION_SCREEN_ON)
                addAction(Intent.ACTION_USER_PRESENT)
            }, ContextCompat.RECEIVER_NOT_EXPORTED)
            registered = true
            getSystemService(AppOpsManager::class.java).startWatchingMode(
                AppOpsManager.OPSTR_SYSTEM_ALERT_WINDOW, packageName, permissionListener)
            publishState()
            updateVisibility()
        } catch (e: Exception) {
            Log.e(TAG, "Pet startup failed", e)
            fail(if (e is SecurityException) R.string.permission_required else R.string.assets_failed)
        }
    }

    private fun interactive() = getSystemService(PowerManager::class.java).isInteractive &&
        !getSystemService(KeyguardManager::class.java).isKeyguardLocked

    /** One place for the observable state the settings screen reads. */
    private fun publishState() {
        PetRuntime.state.value = RuntimeState(running = started, paused = userPaused,
            error = PetRuntime.state.value.error, companions = companionCount(),
            modules = registry?.activeIds()?.sorted() ?: emptyList())
    }

    private fun updateVisibility() {
        if (!started) return
        if (!Settings.canDrawOverlays(this)) { fail(R.string.permission_required); return }
        val visible = interactive()
        val paused = userPaused || !visible
        petMap.values.forEach { it.engine.paused = paused }
        publishState()
        Choreographer.getInstance().removeFrameCallback(this)
        frameScheduled = false
        lastFrameNanos = 0L
        if (!visible) {
            rememberPositions()
            petMap.values.forEach { it.engine.cancelDrag() }
            removeWindows()
            return
        }
        if (surfaces.isEmpty()) attachPets()
        if (surfaces.isEmpty()) return
        renderAll()
        if (!userPaused) scheduleFrame()
    }

    private fun scaleFor(size: Float) =
        112.0 * resources.displayMetrics.density * size / catalog.body.height

    private fun companionScale() = motherScale * CompanionCodec.COMPANION_SCALE

    private fun scaleOf(handle: PetHandle) = if (handle.isCompanion) companionScale() else motherScale

    private fun resizeEngine(engine: PetEngine, scale: Double) {
        engine.resize(max(0.0, safe.width() / scale - catalog.body.width),
            max(0.0, safe.height() / scale - catalog.body.height))
    }

    private fun layout(width: Int, height: Int) = WindowManager.LayoutParams(
        width, height, WindowManager.LayoutParams.TYPE_APPLICATION_OVERLAY,
        WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE or
            WindowManager.LayoutParams.FLAG_NOT_TOUCH_MODAL or WindowManager.LayoutParams.FLAG_LAYOUT_IN_SCREEN,
        PixelFormat.TRANSLUCENT).apply {
        gravity = Gravity.TOP or Gravity.START
        if (Build.VERSION.SDK_INT >= 30) setFitInsetsTypes(0)
    }

    // ---- pets and surfaces -------------------------------------------------

    private fun attachPets() {
        if (loader == null) {
            loader = FrameLoader(assets, ::renderAll, { onFramesFailed(it) },
                FRAME_BUDGET_BYTES, LOOKAHEAD, DECODE_THREADS)
            warmFrames()
        }
        for (handle in petMap.values.toList()) {
            if (!attach(handle)) return
        }
    }

    private fun attach(handle: PetHandle): Boolean {
        if (surfaces.containsKey(handle.id)) return true
        val scale = scaleOf(handle)
        resizeEngine(handle.engine, scale)
        val engine = handle.engine
        val placement = WindowGeometry.placement(engine.x, engine.y, catalog.body,
            catalog.unionCrop, scale, safe.left, safe.top)
        val surface = Surface(handle)
        surface.scale = scale
        surface.cursor = loader?.cursor(handle.id)
        surface.dismissBubble = Runnable { surface.bubble?.visibility = View.INVISIBLE }
        return try {
            val view = PetView(this, handle.id, engine, this)
            view.configure(catalog.unionCrop, scale)
            val params = layout(placement.width, placement.height).apply {
                x = placement.x
                y = placement.y
            }
            windows.addView(view, params)
            surface.view = view
            surface.params = params
            surfaces[handle.id] = surface
            PetRuntime.windowCount.set(surfaces.size)
            render(surface)
            true
        } catch (e: RuntimeException) {
            Log.e(TAG, "Cannot attach overlay", e)
            surface.cursor?.close()
            fail(R.string.permission_required)
            false
        }
    }

    private fun detachSurface(id: Int) {
        val surface = surfaces.remove(id) ?: return
        surface.dismissBubble?.let { handler.removeCallbacks(it) }
        removeAuxiliary(surface.bubble)
        surface.bubble = null
        removeAuxiliary(surface.view)
        surface.view = null
        surface.params = null
        surface.cursor?.close()
        surface.cursor = null
        if (actionsPetId == id) closeMenu()
        PetRuntime.windowCount.set(surfaces.size)
    }

    private fun removeWindows() {
        closeMenu()
        surfaces.keys.toList().forEach { detachSurface(it) }
        releaseLoader()
        PetRuntime.windowCount.set(0)
    }

    private fun releaseLoader() {
        loader?.let {
            decodedTotal += it.decoded.get()
            reusedTotal += it.reused.get()
            hitsTotal += it.cacheHits.get()
            missesTotal += it.cacheMisses.get()
            it.close()
        }
        loader = null
    }

    private fun warmFrames() {
        loader?.warm(catalog.clipsIn("click") + catalog.clipsIn("drag"), WARM_FRAMES)
    }

    private fun onFramesFailed(error: Exception) {
        Log.e(TAG, "Frame decode failed", error)
        fail(R.string.assets_failed)
    }

    // ---- companions --------------------------------------------------------

    private fun companionIds() = petMap.keys.filter { it != 0 }.sorted()

    override fun companionCount() = companionIds().size

    override fun spawnCompanion(): Boolean {
        if (!started) return false
        if (companionCount() >= CompanionCodec.MAX_COMPANIONS) return false
        val mother = petMap[0] ?: return false
        val id = (1..CompanionCodec.MAX_COMPANIONS).firstOrNull { !petMap.containsKey(it) } ?: return false
        val engine = PetEngine(catalog.clips)
        engine.wander = settings.wander
        resizeEngine(engine, companionScale())
        val index = companionCount() + 1
        engine.placeAt(mother.engine.x + catalog.body.width + CompanionCodec.spawnOffset(index),
            mother.engine.y)
        val handle = PetHandle(id, engine)
        petMap[id] = handle
        if (surfaces.isNotEmpty() || interactive()) attach(handle)
        registry?.onEvent(PetEvent.Spawned(handle))
        rememberPositions()
        publishState()
        renderAll()
        return true
    }

    override fun exitCompanion(pet: PetHandle): Boolean {
        val id = if (pet.isCompanion) pet.id else companionIds().lastOrNull() ?: return false
        val handle = petMap.remove(id) ?: return false
        detachSurface(id)
        registry?.onEvent(PetEvent.Exited(handle))
        rememberPositions()
        publishState()
        renderAll()
        return true
    }

    private fun exitAllCompanions() {
        companionIds().toList().forEach { id -> petMap[id]?.let { exitCompanion(it) } }
    }

    private fun restoreCompanions() {
        if (!settings.companions) return
        for ((x, y) in preferences.companionLayout()) {
            val id = (1..CompanionCodec.MAX_COMPANIONS).firstOrNull { !petMap.containsKey(it) } ?: break
            val engine = PetEngine(catalog.clips)
            engine.wander = settings.wander
            resizeEngine(engine, companionScale())
            engine.restorePosition(x, y)
            petMap[id] = PetHandle(id, engine)
        }
    }

    private fun rememberPositions() {
        // onDestroy saves once before shutdown; module cleanup must not erase that household.
        if (!started) return
        petMap[0]?.let { preferences.savePosition(it.engine.normalizedX, it.engine.normalizedY) }
        preferences.saveCompanionLayout(companionIds().mapNotNull { id ->
            petMap[id]?.let { it.engine.normalizedX to it.engine.normalizedY }
        })
    }

    // ---- module host -------------------------------------------------------

    override val options: PetOptions get() = settings

    override val builtinPhrases: List<String> get() = catalog.phrases

    override val pets: List<PetHandle> get() = petMap.values.toList()

    override val bodyBox: SourceBox get() = catalog.body

    /** The collision module needs both pets in one space; the mother is the unit. */
    override fun scaleRatio(pet: PetHandle): Double {
        val mother = motherScale
        return if (mother > 0.0) scaleOf(pet) / mother else 1.0
    }

    override val context: Context get() = this

    override fun now(): Long = SystemClock.elapsedRealtime()

    override fun effectivePhrases(): List<String> =
        PhraseBook.resolve(settings.phrases, catalog.phrases)

    override fun showPhrase(pet: PetHandle, text: String, durationMs: Long) {
        if (!started || userPaused) return
        val surface = surfaces[pet.id] ?: return
        val view = surface.bubble ?: createBubble(surface) ?: return
        view.text = text
        PetRuntime.lastPhrase.set(text)
        if (view.visibility != View.VISIBLE) {
            view.visibility = View.VISIBLE
            view.post { positionBubble(surface) }
        }
        val dismiss = surface.dismissBubble ?: return
        handler.removeCallbacks(dismiss)
        handler.postDelayed(dismiss, durationMs.coerceAtLeast(500))
        positionBubble(surface)
    }

    override fun clearPhrase(pet: PetHandle) {
        val surface = surfaces[pet.id] ?: return
        surface.dismissBubble?.let { handler.removeCallbacks(it) }
        surface.bubble?.visibility = View.INVISIBLE
    }

    /** The desktop's Q 弹 pulse, triggered by a tap (in the view) or a real hit. */
    override fun squash(pet: PetHandle) {
        surfaces[pet.id]?.view?.squash()
    }

    private fun createBubble(surface: Surface): TextView? {
        val view = TextView(this).apply {
            setTextColor(Color.rgb(29, 42, 47)); textSize = 14f
            maxWidth = (220 * resources.displayMetrics.density).roundToInt()
            val pad = (12 * resources.displayMetrics.density).roundToInt()
            setPadding(pad, pad / 2, pad, pad / 2)
            background = GradientDrawable().apply {
                setColor(Color.WHITE); cornerRadius = 8 * resources.displayMetrics.density
                setStroke(1, Color.rgb(213, 224, 226))
            }
            visibility = View.INVISIBLE
        }
        val p = layout(WindowManager.LayoutParams.WRAP_CONTENT, WindowManager.LayoutParams.WRAP_CONTENT)
        // Android 12+ only permits pass-through overlays below its opacity limit.
        p.flags = p.flags or WindowManager.LayoutParams.FLAG_NOT_TOUCHABLE
        p.alpha = .8f
        return try {
            windows.addView(view, p)
            surface.bubble = view
            PetRuntime.bubbles.incrementAndGet()
            view
        } catch (e: RuntimeException) {
            Log.e(TAG, "Cannot attach bubble", e)
            fail(R.string.permission_required)
            null
        }
    }

    /** One bubble window per pet, created once and only retexted afterwards. */
    private fun positionBubble(surface: Surface) {
        val view = surface.bubble ?: return
        if (view.visibility != View.VISIBLE) return
        val p = view.layoutParams as? WindowManager.LayoutParams ?: return
        val engine = surface.handle.engine
        val x = (safe.left + engine.x * surface.scale).roundToInt()
            .coerceIn(safe.left, max(safe.left, safe.right - view.width))
        val above = (safe.top + engine.y * surface.scale - view.height -
            8 * resources.displayMetrics.density).roundToInt()
        val y = if (above >= safe.top) above else
            (safe.top + (engine.y + catalog.body.height) * surface.scale).roundToInt()
                .coerceAtMost(safe.bottom - view.height)
        if (p.x == x && p.y == y) return
        p.x = x; p.y = y
        try {
            windows.updateViewLayout(view, p)
            PetRuntime.windowOps.incrementAndGet()
        } catch (_: RuntimeException) {
            removeAuxiliary(view)
            surface.bubble = null
        }
    }

    // ---- frame loop --------------------------------------------------------

    private fun scheduleFrame() {
        if (!frameScheduled && started && !userPaused && surfaces.isNotEmpty()) {
            frameScheduled = true
            Choreographer.getInstance().postFrameCallback(this)
        }
    }

    /** 24 fps while an idle clip plays, 60 Hz while a pet is moving or flying. */
    private fun frameIntervalSeconds(): Double {
        val moving = surfaces.values.any {
            val mode = it.handle.engine.mode
            mode == PetMode.MOVE || mode == PetMode.THROWN
        }
        val fps = if (moving) 60.0 else surfaces.values.firstOrNull()?.handle?.engine?.clip?.fps ?: 24.0
        return 1.0 / fps.coerceIn(1.0, 60.0)
    }

    override fun doFrame(frameTimeNanos: Long) {
        frameScheduled = false
        if (!started || userPaused || surfaces.isEmpty()) return
        if (surfaces.values.first().handle.engine.paused) return
        if (lastFrameNanos == 0L) lastFrameNanos = frameTimeNanos
        val elapsed = (frameTimeNanos - lastFrameNanos) / 1e9
        if (elapsed >= frameIntervalSeconds()) {
            lastFrameNanos = frameTimeNanos
            // Collisions settle on the pre-integration snapshot, like the desktop.
            registry?.onTick(elapsed)
            for (surface in surfaces.values.toList()) {
                surface.handle.engine.advance(elapsed)
                render(surface)
                registry?.onFrame(surface.handle, elapsed)
            }
        }
        scheduleFrame()
    }

    private fun renderAll() {
        surfaces.values.toList().forEach { render(it) }
    }

    private fun render(surface: Surface) {
        val view = surface.view ?: return
        val params = surface.params ?: return
        val engine = surface.handle.engine
        val clip = engine.clip
        val placement = WindowGeometry.placement(engine.x, engine.y, catalog.body,
            catalog.unionCrop, surface.scale, safe.left, safe.top)
        if (params.x != placement.x || params.y != placement.y ||
            params.width != placement.width || params.height != placement.height) {
            params.x = placement.x; params.y = placement.y
            params.width = placement.width; params.height = placement.height
            try {
                windows.updateViewLayout(view, params)
                PetRuntime.windowOps.incrementAndGet()
            } catch (e: RuntimeException) {
                fail(R.string.permission_required)
                return
            }
        }
        val frame = surface.cursor?.request(clip, engine.frameIndex)
        if (frame == null) PetRuntime.staleFrames.incrementAndGet()
        view.present(frame, clip.crop, engine.facingRight)
        positionBubble(surface)
    }

    // ---- view callbacks ----------------------------------------------------

    override fun onChanged(petId: Int) {
        surfaces[petId]?.let { render(it) }
    }

    override fun onPressed(petId: Int) {
        surfaces[petId]?.let { registry?.onEvent(PetEvent.Pressed(it.handle)) }
    }

    override fun onReleased(petId: Int) {
        rememberPositions()
        closeMenu()
    }

    override fun onClicked(petId: Int) {
        surfaces[petId]?.let {
            registry?.onEvent(PetEvent.Clicked(it.handle))
            render(it)
        }
    }

    override fun onMenu(petId: Int) {
        showActions(petId)
    }

    // ---- long-press menu ---------------------------------------------------

    private fun showActions(petId: Int) {
        closeMenu()
        val surface = surfaces[petId] ?: return
        val panel = object : LinearLayout(this) {
            override fun dispatchKeyEvent(event: KeyEvent): Boolean {
                if (event.keyCode == KeyEvent.KEYCODE_BACK) {
                    if (event.action == KeyEvent.ACTION_UP) closeMenu()
                    return true
                }
                return super.dispatchKeyEvent(event)
            }
        }.apply {
            orientation = LinearLayout.VERTICAL
            setBackgroundColor(Color.WHITE)
            elevation = 6 * resources.displayMetrics.density
        }
        fun action(label: Int, run: () -> Unit) {
            panel.addView(Button(this).apply {
                setText(label)
                setOnClickListener { closeMenu(); run() }
            })
        }
        action(R.string.settings) {
            startActivity(Intent(this, MainActivity::class.java).addFlags(
                Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_CLEAR_TOP))
        }
        action(if (userPaused) R.string.resume else R.string.pause) {
            command(this, if (userPaused) RESUME else PAUSE)
        }
        registry?.menuItems(surface.handle)?.forEach { item ->
            action(item.labelRes) { item.action() }
        }
        action(R.string.stop) { stopSelf() }
        val p = layout((160 * resources.displayMetrics.density).roundToInt(),
            WindowManager.LayoutParams.WRAP_CONTENT)
        // A user-opened menu needs focus for Back and accessibility navigation.
        p.flags = p.flags and WindowManager.LayoutParams.FLAG_NOT_FOCUSABLE.inv()
        p.flags = p.flags or WindowManager.LayoutParams.FLAG_WATCH_OUTSIDE_TOUCH
        panel.setOnTouchListener { _, event ->
            if (event.action == MotionEvent.ACTION_OUTSIDE) closeMenu()
            false
        }
        val engine = surface.handle.engine
        p.x = (safe.left + engine.x * surface.scale).roundToInt()
            .coerceIn(safe.left, max(safe.left, safe.right - p.width))
        p.y = (safe.top + engine.y * surface.scale).roundToInt().coerceIn(safe.top,
            max(safe.top, safe.bottom - (170 * resources.displayMetrics.density).roundToInt()))
        try {
            windows.addView(panel, p)
            actions = panel
            actionsPetId = petId
        } catch (e: RuntimeException) {
            fail(R.string.permission_required)
        }
    }

    private fun closeMenu() {
        removeAuxiliary(actions)
        actions = null
        actionsPetId = -1
    }

    // ---- preferences and system -------------------------------------------

    private fun reloadOptions() {
        if (!started) return
        settings = preferences.options()
        safe = safeBounds()
        motherScale = scaleFor(settings.size)
        for (handle in petMap.values.toList()) resizeEngine(handle.engine, scaleOf(handle))
        for (surface in surfaces.values.toList()) {
            surface.scale = scaleOf(surface.handle)
            surface.view?.configure(catalog.unionCrop, surface.scale)
        }
        registry?.apply()
        if (!settings.speech) {
            surfaces.values.toList().forEach { surface ->
                surface.dismissBubble?.let { handler.removeCallbacks(it) }
                surface.bubble?.visibility = View.INVISIBLE
            }
        }
        publishState()
        renderAll()
    }

    @Suppress("DEPRECATION")
    private fun safeBounds(): Rect {
        if (Build.VERSION.SDK_INT >= 30) {
            val metrics = windows.currentWindowMetrics
            val insets = metrics.windowInsets.getInsetsIgnoringVisibility(
                WindowInsets.Type.systemBars() or WindowInsets.Type.displayCutout())
            return Rect(metrics.bounds).apply {
                left += insets.left; top += insets.top; right -= insets.right; bottom -= insets.bottom
            }
        }
        val metrics = DisplayMetrics()
        windows.defaultDisplay.getRealMetrics(metrics)
        fun inset(name: String): Int {
            val id = resources.getIdentifier(name, "dimen", "android")
            return if (id == 0) 0 else resources.getDimensionPixelSize(id)
        }
        return Rect(0, inset("status_bar_height"), metrics.widthPixels,
            metrics.heightPixels - inset("navigation_bar_height"))
    }

    private fun notification(): Notification {
        fun pending(action: String) = PendingIntent.getService(this, action.hashCode(),
            Intent(this, PetOverlayService::class.java).setAction(action),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT)
        return NotificationCompat.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_menu_compass)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(getString(if (userPaused) R.string.paused else R.string.running))
            .setOngoing(true).setOnlyAlertOnce(true).setSilent(true)
            .setContentIntent(PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java),
                PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT))
            .addAction(if (userPaused) android.R.drawable.ic_media_play else android.R.drawable.ic_media_pause,
                getString(if (userPaused) R.string.resume else R.string.pause), pending(if (userPaused) RESUME else PAUSE))
            .addAction(android.R.drawable.ic_menu_close_clear_cancel, getString(R.string.stop), pending(STOP))
            .build()
    }

    private fun updateNotification() = getSystemService(NotificationManager::class.java).notify(1, notification())

    private fun fail(message: Int) {
        PetRuntime.state.value = RuntimeState(error = getString(message))
        Toast.makeText(this, message, Toast.LENGTH_LONG).show()
        stopSelf()
    }

    private fun removeAuxiliary(view: View?) {
        if (view != null) try { windows.removeViewImmediate(view) } catch (_: RuntimeException) { }
    }

    override fun onConfigurationChanged(newConfig: Configuration) {
        super.onConfigurationChanged(newConfig)
        if (started) { reloadOptions(); rememberPositions() }
    }

    override fun onDestroy() {
        if (started) rememberPositions()
        started = false
        Choreographer.getInstance().removeFrameCallback(this)
        handler.removeCallbacksAndMessages(null)
        preferences.unsubscribe(preferenceListener)
        if (registered) {
            unregisterReceiver(screenReceiver)
            getSystemService(AppOpsManager::class.java).stopWatchingMode(permissionListener)
        }
        registry?.shutdown()
        registry = null
        removeWindows()
        PetRuntime.state.value = RuntimeState(error = PetRuntime.state.value.error)
        stopForeground(STOP_FOREGROUND_REMOVE)
        super.onDestroy()
    }

    override fun dump(fd: FileDescriptor, writer: PrintWriter, args: Array<out String>) {
        val active = loader
        writer.println("running=$started paused=$userPaused interactive=${interactive()}")
        writer.println("pets=${petMap.size} windows=${PetRuntime.windowCount.get()} draws=${PetRuntime.draws.get()}")
        writer.println("windowOps=${PetRuntime.windowOps.get()} bubbles=${PetRuntime.bubbles.get()} " +
            "stale=${PetRuntime.staleFrames.get()} audio=${PetRuntime.audioPlays.get()}")
        writer.println("decoded=${decodedTotal + (active?.decoded?.get() ?: 0)} " +
            "reused=${reusedTotal + (active?.reused?.get() ?: 0)} " +
            "hits=${hitsTotal + (active?.cacheHits?.get() ?: 0)} " +
            "misses=${missesTotal + (active?.cacheMisses?.get() ?: 0)} " +
            "cached=${active?.cachedFrames() ?: 0} cacheKB=${(active?.cachedBytes() ?: 0) / 1024}")
        writer.println("modules=${registry?.activeIds() ?: emptySet()} " +
            "ticks=${registry?.ticksDispatched ?: 0} frames=${registry?.framesDispatched ?: 0} " +
            "customPhrases=${settings.phrases.size}")
        writer.println("moduleInfo=${registry?.diagnostics()}")
        if (started) petMap[0]?.let {
            writer.println("mode=${it.engine.mode} x=${it.engine.x} y=${it.engine.y} clip=${it.engine.clip.id}")
        }
        writer.println("phrase=${PetRuntime.lastPhrase.get()}")
        writer.println("gesture=${surfaces[0]?.view?.lastGesture}")
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
