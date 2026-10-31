// 审批放行后的「续跑」判据：同一条命令 + 非危险装饰必须自动放行。
//
// 真机现场（2026-11-01）：允许 `Remove-Item … -Force` 后，DSH 重发时会追加
// `; "exit=$LASTEXITCODE"`，argsHash 变了 → 又弹一张卡，模型在会话里看到
// 「还是被拦住了，这是第四次」。只比哈希等于永远问不完。
import test from 'node:test'
import assert from 'node:assert/strict'

import { sameCommandFamily } from '../integrations/dsh-pet-bridge/channel.js'

test('identical command is the same operation', () => {
  assert.equal(
    sameCommandFamily('Remove-Item -Path A', 'Remove-Item -Path A'),
    true,
  )
})

test('whitespace differences do not change the operation', () => {
  assert.equal(
    sameCommandFamily('Remove-Item  -Path A', 'Remove-Item -Path   A'),
    true,
  )
})

test('an appended exit-code probe is still the same operation', () => {
  assert.equal(
    sameCommandFamily(
      'Remove-Item -LiteralPath E:/tmp/x -Force',
      'Remove-Item -LiteralPath E:/tmp/x -Force; "exit=$LASTEXITCODE"',
    ),
    true,
  )
})

test('appending another destructive command must ask again', () => {
  assert.equal(
    sameCommandFamily('Remove-Item A', 'Remove-Item A; Remove-Item B'),
    false,
  )
  assert.equal(
    sameCommandFamily('Remove-Item A', 'Remove-Item A && del B'),
    false,
  )
})

test('different or unrelated commands are not the same operation', () => {
  assert.equal(sameCommandFamily('Remove-Item A', 'Remove-Item B'), false)
  assert.equal(sameCommandFamily('Remove-Item A', ''), false)
  assert.equal(sameCommandFamily('', 'Remove-Item A'), false)
})
