# -*- coding: utf-8 -*-
"""歌词提前量的默认值与可调范围（零 Qt、零第三方依赖的常量模块）。

为什么要单独一层：这些常量同时被 ``config.py``（归一化 ``music_lyric_lead_seconds``）
与设置页滑块（``settings_pet_controls``）读取，而消费者 ``pet/music/lyric_controller.py``
为了实现取词/播放会导入 ``now_playing``（asyncio + urllib）与 ``window_alerts``。
只为三个数字就把整条歌词控制链拖进启动路径不划算——常量独立成轻模块后，
主进程与独立设置进程的开头都少一段导入（本机实测 Config() 少 100ms 量级）。

``pet/music/lyric_controller.py`` 仍原样 re-export 这三个名字，既有导入路径不变。
"""

# 歌词提前量（秒）的默认值与可调范围。实机实测歌词比音频晚不到 1 秒；
# 用户明确表示宁可歌词早、不能接受歌词晚，故默认提前 1 秒。
# 允许负值：如果某些播放器反而偏早，用户可往回调。
LYRIC_LEAD_SECONDS = 1.0
LEAD_MIN_SECONDS = -2.0
LEAD_MAX_SECONDS = 3.0