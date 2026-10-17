package io.github.dshpet.mobile.ui

import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardCapitalization
import androidx.compose.ui.text.input.KeyboardType
import io.github.dshpet.mobile.PetOptions
import io.github.dshpet.mobile.R

/**
 * 电脑端 DSH 联动：开关 + 端点地址 + 配对令牌。
 *
 * 三个值都是普通手机偏好（`dshLink` / `dshLinkUrl` / `dshLinkToken`）；开关就是
 * 模块的启用位，关闭时模块不会被创建。地址与令牌在离开输入框或按「保存」时提交，
 * 与台词池同一套提交口径。
 */
@Composable
fun DshLinkSection(options: PetOptions, onChanged: (PetOptions) -> Unit) {
    SettingSwitch(R.string.dsh_link, options.dshLink) { onChanged(options.copy(dshLink = it)) }
    if (options.dshLink) {
        var urlDraft by remember(options.dshLinkUrl) { mutableStateOf(options.dshLinkUrl) }
        var tokenDraft by remember(options.dshLinkToken) { mutableStateOf(options.dshLinkToken) }

        OutlinedTextField(
            value = urlDraft,
            onValueChange = { urlDraft = it },
            modifier = Modifier.fillMaxWidth().onFocusChanged {
                if (!it.isFocused && urlDraft != options.dshLinkUrl) {
                    onChanged(options.copy(dshLinkUrl = urlDraft.trim()))
                }
            },
            singleLine = true,
            label = { Text(stringResource(R.string.dsh_link_url)) },
            placeholder = { Text(stringResource(R.string.dsh_link_url_hint)) },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri,
                capitalization = KeyboardCapitalization.None, imeAction = ImeAction.Next),
        )
        OutlinedTextField(
            value = tokenDraft,
            onValueChange = { tokenDraft = it },
            modifier = Modifier.fillMaxWidth().onFocusChanged {
                if (!it.isFocused && tokenDraft != options.dshLinkToken) {
                    onChanged(options.copy(dshLinkToken = tokenDraft.trim()))
                }
            },
            singleLine = true,
            label = { Text(stringResource(R.string.dsh_link_token)) },
            placeholder = { Text(stringResource(R.string.dsh_link_token_hint)) },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password,
                capitalization = KeyboardCapitalization.None, imeAction = ImeAction.Done),
        )
        TextButton(onClick = {
            onChanged(options.copy(dshLinkUrl = urlDraft.trim(), dshLinkToken = tokenDraft.trim()))
        }, enabled = urlDraft.trim() != options.dshLinkUrl || tokenDraft.trim() != options.dshLinkToken) {
            Text(stringResource(R.string.dsh_link_save))
        }
        Text(stringResource(R.string.dsh_link_hint))
    }
}
