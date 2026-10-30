import { apply, inject, name } from './index.js'

if (name !== 'pet-channel') {
  console.error('unexpected name', name)
  process.exit(1)
}
if (!Array.isArray(inject) || !inject.includes('tools')) {
  console.error('missing tools injection', inject)
  process.exit(1)
}
if (typeof apply !== 'function') {
  console.error('missing apply')
  process.exit(1)
}
console.log('dsh-pet-channel import ok')
