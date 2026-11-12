"""Locate the clock and player object inside a verified x64 CloudMusic image.

Instruction signatures are also documented by AF-Media-Bar (MIT; see
THIRD_PARTY_NOTICES.md). The PE parser and reader here are independent Python
implementations. Only executable sections are searched, once per DLL identity;
object-field layouts still require a tested build, not just a plausible match.
"""
from dataclasses import dataclass
from functools import lru_cache
import hashlib
from pathlib import Path
import re
import struct


# Verified object layout: CloudMusic 3.1.42.205594, Windows x64.
SUPPORTED_IMAGES = frozenset({
    '557d7706dedc8556a8bd279d4dfe2bc05b80ad729a8749caccd9e29916935493',
})
SCHEDULE_PATTERN = '66 0F 2E 0D ? ? ? ? 7A ? 75 ? 66 0F 2E 15'
PLAYER_PATTERN = (
    '48 8D 0D ? ? ? ? E8 ? ? ? ? 48 8D 0D ? ? ? ? E8 ? ? ? ? 90 '
    '48 8D 0D ? ? ? ? E8 ? ? ? ? 48 8D 05 ? ? ? ? 48 8D A5 ? ? ? ? '
    '5F 5D C3 CC CC CC CC CC 48 89 4C 24 ? 55 57 48 81 EC ? ? ? ? '
    '48 8D 6C 24 ? 48 8D 7C 24'
)


@dataclass(frozen=True)
class Layout:
    image_size: int
    position_rva: int
    player_rva: int
    instructions: tuple[tuple[int, bytes], ...]


def inspect_image(data: bytes) -> Layout:
    """Resolve unique RIP-relative references; reject truncated/ambiguous images."""
    if len(data) < 64 or data[:2] != b'MZ':
        raise ValueError('invalid_pe')
    pe, = struct.unpack_from('<I', data, 0x3c)
    if pe + 24 > len(data) or data[pe:pe + 4] != b'PE\0\0':
        raise ValueError('invalid_pe')
    machine, count = struct.unpack_from('<HH', data, pe + 4)
    optional_size, = struct.unpack_from('<H', data, pe + 20)
    table = pe + 24 + optional_size
    if machine != 0x8664 or optional_size < 112 or table + count * 40 > len(data):
        raise ValueError('unsupported_pe')
    if struct.unpack_from('<H', data, pe + 24)[0] != 0x20b:
        raise ValueError('unsupported_pe')
    image_size, = struct.unpack_from('<I', data, pe + 24 + 56)
    sections = []
    for i in range(count):
        offset = table + 40 * i
        virtual_size, rva, size, raw = struct.unpack_from('<IIII', data, offset + 8)
        flags, = struct.unpack_from('<I', data, offset + 36)
        if raw + size > len(data) or rva + max(size, virtual_size) > image_size:
            raise ValueError('invalid_section')
        sections.append((rva, max(size, virtual_size), raw, size, flags))

    references = []
    targets = []
    for pattern, displacement_offset, target_size in (
        (SCHEDULE_PATTERN, 4, 8), (PLAYER_PATTERN, 3, 0xb0),
    ):
        regex = re.compile(b''.join(
            b'.' if part == '?' else re.escape(bytes([int(part, 16)]))
            for part in pattern.split()
        ), re.DOTALL)
        matches = []
        for rva, _, raw, size, flags in sections:
            if not flags & 0x20000000:  # IMAGE_SCN_MEM_EXECUTE
                continue
            for match in regex.finditer(data, raw, raw + size):
                instruction = rva + match.start() - raw
                displacement, = struct.unpack_from('<i', data, match.start() + displacement_offset)
                target = instruction + displacement_offset + 4 + displacement
                matches.append((instruction, match.group(), target))
        if len(matches) != 1:
            raise ValueError('ambiguous_signature')
        instruction, code, target = matches[0]
        if not any(rva <= target and target + target_size <= rva + size
                   and flags & 0x80000000 and not flags & 0x20000000
                   for rva, size, _, _, flags in sections):
            raise ValueError('invalid_data_target')
        references.append((instruction, code))
        targets.append(target)
    return Layout(image_size, targets[0], targets[1], tuple(references))


@lru_cache(maxsize=4)
def _layout_at(path: str, size: int, modified_ns: int) -> Layout | None:
    if not 0 < size <= 128 * 1024 * 1024:
        return None
    with open(path, 'rb') as stream:
        data = stream.read(size + 1)
    if len(data) != size or hashlib.sha256(data).hexdigest() not in SUPPORTED_IMAGES:
        return None
    try:
        return inspect_image(data)
    except (ValueError, struct.error):
        return None


def file_layout(path: str) -> Layout | None:
    stat = Path(path).stat()
    return _layout_at(path, stat.st_size, stat.st_mtime_ns)
