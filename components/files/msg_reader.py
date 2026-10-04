"""Minimal dependency-free .msg (Outlook email) reader for File Workbench previews.

Parses the OLE compound document layout of .msg files, tolerating the
non-standard headers written by some Chinese email exporters (FAT sectors
contiguous from sector 0, mini-stream cutoff really 4096, directory located
one sector after the field suggests).  Extracts subject, sender, recipients,
dates, body (plain text or HTML) and attachment names from either top-level
__substg1.0_* streams or the binary __properties_version1.0 stream.
"""

from __future__ import annotations

import re
import struct
from datetime import datetime, timedelta
from pathlib import Path

_FREE = 0xFFFFFFFE
_ENDOFCHAIN = 0xFFFFFFFD
_FATSECT = 0xFFFFFFFF
_DIFSECT = 0xFFFFFFFC
_OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_EPOCH_FILETIME = 116444736000000000
_MIN_SECTOR_SHIFT = 9
_MAX_SECTOR_SHIFT = 12


def _filetime(ticks: int) -> str:
    try:
        seconds = (ticks - _EPOCH_FILETIME) / 10_000_000
        return (datetime(1970, 1, 1) + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M")
    except (OverflowError, OSError, ValueError):
        return ""


class _OleStorage:
    """Minimal Compound File Binary reader sufficient for .msg files."""

    def __init__(self, path: Path | str) -> None:
        self.data = Path(path).read_bytes()
        if self.data[:8] != _OLE_MAGIC:
            raise ValueError("not an OLE compound file")
        major = struct.unpack_from("<H", self.data, 26)[0]
        sector_shift = struct.unpack_from("<H", self.data, 30)[0]
        if major not in (3, 4) or not (_MIN_SECTOR_SHIFT <= sector_shift <= _MAX_SECTOR_SHIFT):
            raise ValueError("unsupported OLE version")
        self.sector_size = 1 << sector_shift
        self.mini_size = 1 << struct.unpack_from("<H", self.data, 32)[0]
        self.num_fat = struct.unpack_from("<I", self.data, 48)[0]
        self.dir_start = struct.unpack_from("<I", self.data, 52)[0]
        mini_cutoff = struct.unpack_from("<I", self.data, 60)[0]
        self.mini_cutoff = mini_cutoff if (512 <= mini_cutoff <= 65536 and (mini_cutoff & (mini_cutoff - 1)) == 0) else 4096
        self.minifat_start = struct.unpack_from("<I", self.data, 64)[0]
        self.num_minifat = struct.unpack_from("<I", self.data, 68)[0]
        self.num_sectors = (len(self.data) - self.sector_size) // self.sector_size
        self.fat = self._build_fat()
        self.dir_entries = self._build_dir_entries()
        self.children: dict[int, list[int]] = self._build_tree()
        self.mini_stream, self.minifat = self._build_mini()

    def _sector(self, number: int) -> bytes:
        offset = self.sector_size + number * self.sector_size
        return self.data[offset:offset + self.sector_size]

    def _chain(self, start: int, table: list[int]) -> list[int]:
        result: list[int] = []
        seen: set[int] = set()
        sector = start
        while (sector not in (_FREE, _ENDOFCHAIN, _FATSECT, _DIFSECT)
               and sector < len(table) and sector not in seen):
            result.append(sector)
            seen.add(sector)
            sector = table[sector]
        return result

    def _stream_regular(self, start: int, fat: list[int] | None = None) -> bytes:
        return b"".join(self._sector(number) for number in self._chain(start, fat if fat is not None else self.fat))

    def _read_fat(self, fat_sectors: list[int]) -> list[int]:
        fat: list[int] = []
        for fat_sector in fat_sectors:
            offset = self.sector_size + fat_sector * self.sector_size
            fat.extend(struct.unpack_from("<%dI" % (self.sector_size // 4), self.data, offset))
        return fat

    def _score_fat(self, fat: list[int]) -> int:
        """Prefer the FAT layout that yields the fullest, valid directory."""
        best = 0
        for start in (self.dir_start, self.dir_start + 1):
            entries = 0
            root_size = -1
            root_start = -1
            for number in self._chain(start, fat):
                for entry in self._parse_dir_sector(self._sector(number)):
                    entries += 1
                    if entry["name"] == "Root Entry":
                        root_size = int(entry["size"])
                        root_start = int(entry["start"])
            if entries < 4 or root_start < 0:
                continue
            mini_len = min(len(self._stream_regular(root_start, fat)), root_size)
            best = max(best, entries * 1000 - abs(root_size - mini_len) // 512)
        return best

    def _build_fat(self) -> list[int]:
        difat_start = struct.unpack_from("<I", self.data, 72)[0]
        num_difat = struct.unpack_from("<I", self.data, 76)[0]
        difat = [value for value in struct.unpack_from("<109I", self.data, 80)
                 if value not in (_FREE, _ENDOFCHAIN, _FATSECT, _DIFSECT) and value < self.num_sectors]
        if num_difat > 0 and difat_start not in (_FREE, _ENDOFCHAIN, _FATSECT, _DIFSECT) and difat_start < self.num_sectors:
            seen: set[int] = set()
            sector = difat_start
            while sector not in (_FREE, _ENDOFCHAIN, _FATSECT, _DIFSECT) and sector < self.num_sectors and sector not in seen:
                seen.add(sector)
                offset = self.sector_size + sector * self.sector_size
                values = struct.unpack_from("<%dI" % (self.sector_size // 4 - 1), self.data, offset)
                nxt = struct.unpack_from("<I", self.data, offset + self.sector_size - 4)[0]
                difat.extend(value for value in values
                             if value not in (_FREE, _ENDOFCHAIN, _FATSECT, _DIFSECT) and value < self.num_sectors)
                sector = nxt
        candidates: list[list[int]] = []
        if difat:
            candidates.append(difat)
            candidates.append([0] + difat)
        entries_per_sector = self.sector_size // 4
        needed = (self.num_sectors + entries_per_sector - 1) // entries_per_sector
        candidates.append(list(range(min(max(self.num_fat, needed), self.num_sectors))))
        unique: list[tuple[int, ...]] = []
        for candidate in candidates:
            key = tuple(candidate)
            if key not in unique:
                unique.append(key)
        best_fat: list[int] = []
        best_score = -1
        for candidate in unique:
            fat = self._read_fat(list(candidate))
            score = self._score_fat(fat)
            if score > best_score:
                best_fat, best_score = fat, score
        if not best_fat:
            best_fat = self._read_fat([0])
        return best_fat

    def _parse_dir_sector(self, sector_data: bytes) -> list[dict[str, object]]:
        result: list[dict[str, object]] = []
        for index in range(self.sector_size // 128):
            entry = sector_data[index * 128:(index + 1) * 128]
            if not entry[:64].strip(b"\x00"):
                continue
            name_length = struct.unpack_from("<H", entry, 64)[0]
            if name_length < 2 or name_length > 64:
                continue
            result.append({
                "name": entry[:name_length - 2].decode("utf-16le", errors="replace"),
                "type": entry[66],
                "left": struct.unpack_from("<I", entry, 68)[0],
                "right": struct.unpack_from("<I", entry, 72)[0],
                "child": struct.unpack_from("<I", entry, 76)[0],
                "start": struct.unpack_from("<I", entry, 116)[0],
                "size": struct.unpack_from("<I", entry, 120)[0],
            })
        return result

    def _find_dir_start(self) -> int:
        for candidate in (self.dir_start, self.dir_start + 1):
            if 0 <= candidate < self.num_sectors:
                entries = self._parse_dir_sector(self._sector(candidate))
                if entries and entries[0]["name"] == "Root Entry":
                    return candidate
        for number in range(self.num_sectors):
            entries = self._parse_dir_sector(self._sector(number))
            if entries and entries[0]["name"] == "Root Entry":
                return number
        raise ValueError("OLE directory not found")

    def _build_dir_entries(self) -> list[dict[str, object]]:
        start = self._find_dir_start()
        entries: list[dict[str, object]] = []
        for number in self._chain(start, self.fat):
            entries.extend(self._parse_dir_sector(self._sector(number)))
        return entries

    def _build_tree(self) -> dict[int, list[int]]:
        """Map each directory entry index to the indices of its direct children."""
        children: dict[int, list[int]] = {}
        by_name = {entry["name"]: index for index, entry in enumerate(self.dir_entries)}
        seen: set[int] = set()

        def walk(index: int, parent: int | None) -> None:
            if index < 0 or index >= len(self.dir_entries) or index in seen:
                return
            if index in (_FREE, _ENDOFCHAIN, _FATSECT, _DIFSECT):
                return
            seen.add(index)
            entry = self.dir_entries[index]
            if parent is not None:
                children.setdefault(parent, []).append(index)
            if int(entry["left"]) != index:
                walk(int(entry["left"]), parent)
            if int(entry["right"]) != index:
                walk(int(entry["right"]), parent)
            if entry["type"] in (1, 5) and int(entry["child"]) != index:
                walk(int(entry["child"]), index)

        root = by_name.get("Root Entry")
        if root is not None:
            walk(root, None)
        return children

    def _read_minifat(self, start: int) -> list[int]:
        minifat: list[int] = []
        seen: set[int] = set()
        sector = start
        while (sector not in (_FREE, _ENDOFCHAIN, _FATSECT, _DIFSECT)
               and sector < len(self.fat) and sector not in seen):
            seen.add(sector)
            offset = self.sector_size + sector * self.sector_size
            minifat.extend(struct.unpack_from("<%dI" % (self.sector_size // 4), self.data, offset))
            sector = self.fat[sector]
        return minifat

    def _build_mini(self) -> tuple[bytes, list[int]]:
        root = next((entry for entry in self.dir_entries if entry["name"] == "Root Entry"), None)
        if root is None:
            return b"", []
        mini_stream = self._stream_regular(int(root["start"]))[:int(root["size"])]
        old_layout_start = struct.unpack_from("<I", self.data, 60)[0]
        candidates: list[int] = []
        for value in (self.minifat_start, old_layout_start, self.minifat_start + 1, 2):
            if 0 <= value < self.num_sectors and value not in candidates:
                candidates.append(value)
        minifat: list[int] = []
        mini_count = len(mini_stream) // self.mini_size + 1
        entry_list = next((entry for entry in self.dir_entries
                           if entry["name"] == "__substg1.0_00020102"), None)
        known_head = b"\x08 \x06\x00\x00\x00\x00\x00\xc0\x00\x00\x00\x00\x00\x00\x46"
        for candidate in candidates:
            if not (0 <= candidate < self.num_sectors):
                continue
            probe = self._read_minifat(candidate)
            if not probe:
                continue
            plausible = sum(1 for value in probe[:mini_count + 8]
                            if value in (_FREE, _ENDOFCHAIN, _FATSECT, _DIFSECT) or value < mini_count)
            if plausible < len(probe[:mini_count + 8]) * 0.9:
                continue  # Not a mini-FAT; the entries look like stream data.
            if entry_list is not None and int(entry_list["size"]) < self.mini_cutoff:
                chunk = b""
                sector = int(entry_list["start"])
                seen: set[int] = set()
                while (sector not in (_FREE, _ENDOFCHAIN, _FATSECT, _DIFSECT)
                       and sector * self.mini_size < len(mini_stream) and sector not in seen):
                    seen.add(sector)
                    chunk += mini_stream[sector * self.mini_size:(sector + 1) * self.mini_size]
                    sector = probe[sector] if sector < len(probe) else _ENDOFCHAIN
                if chunk[:16] != known_head:
                    continue  # Reading the entry-list stream gives the wrong data.
            minifat = probe
            break
        if not minifat:
            minifat = self._read_minifat(candidates[0]) if candidates else []
        return mini_stream, minifat

    def stream(self, entry: dict[str, object]) -> bytes:
        """Read a directory stream, choosing the mini stream for small entries."""
        size = int(entry["size"])
        start = int(entry["start"])
        if size < self.mini_cutoff and self.mini_stream:
            chunks: list[bytes] = []
            sector = start
            seen: set[int] = set()
            while (sector not in (_FREE, _ENDOFCHAIN, _FATSECT, _DIFSECT)
                   and sector * self.mini_size < len(self.mini_stream) and sector not in seen):
                seen.add(sector)
                offset = sector * self.mini_size
                chunks.append(self.mini_stream[offset:offset + self.mini_size])
                sector = self.minifat[sector] if sector < len(self.minifat) else _ENDOFCHAIN
            return b"".join(chunks)[:size]
        return self._stream_regular(start)[:size]

    def child_streams(self, parent_name: str) -> dict[str, bytes]:
        """Return {stream name: bytes} for every stream directly under a storage."""
        result: dict[str, bytes] = {}
        parent = next((index for index, entry in enumerate(self.dir_entries)
                       if entry["name"] == parent_name), None)
        if parent is None:
            return result
        for index in self.children.get(parent, []):
            entry = self.dir_entries[index]
            if entry["type"] == 2:
                result[str(entry["name"])] = self.stream(entry)
        return result


def _decode_ansi(raw: bytes) -> str:
    try:
        text = raw.decode("cp1252", errors="replace")
        if "\ufffd" not in text:
            return text.rstrip("\x00")
    except Exception:
        pass
    return raw.decode("gb18030", errors="replace").rstrip("\x00")


def _read_prop_value(data: bytes, pos: int, prop_type: int) -> tuple[object, int]:
    if prop_type in (0x001F, 0x001E):
        length = struct.unpack_from("<I", data, pos)[0]
        raw = data[pos + 4:pos + 4 + length]
        value: object = raw.decode("utf-16le", errors="replace").rstrip("\x00") if prop_type == 0x001F else _decode_ansi(raw)
        return value, pos + 4 + length
    if prop_type == 0x0102:
        length = struct.unpack_from("<I", data, pos)[0]
        return data[pos + 4:pos + 4 + length], pos + 4 + length
    if prop_type == 0x0040:
        return _filetime(struct.unpack_from("<Q", data, pos)[0]), pos + 8
    if prop_type in (0x0002, 0x0003, 0x000A):
        return struct.unpack_from("<I", data, pos)[0], pos + 4
    if prop_type in (0x0001, 0x000B):
        return struct.unpack_from("<H", data, pos)[0], pos + 2
    if prop_type in (0x0014, 0x0006, 0x0007, 0x000D, 0x000E):
        return struct.unpack_from("<Q", data, pos)[0], pos + 8
    if prop_type == 0x0004:
        return struct.unpack_from("<f", data, pos)[0], pos + 4
    if prop_type == 0x0005:
        return struct.unpack_from("<d", data, pos)[0], pos + 8
    return data[pos:pos + 8], pos + 8


def _parse_property_stream(data: bytes) -> dict[int, object]:
    """Decode a MAPI property stream into {property tag: value}."""
    props: dict[int, object] = {}
    pos = 0
    size = len(data)
    while pos + 8 <= size:
        tag, flags = struct.unpack_from("<II", data, pos)
        pos += 8
        if tag in (0, 0xFFFFFFFF):
            break  # Padding / end-of-stream marker.
        prop_type = tag & 0xFFFF
        try:
            if flags & 1:  # multi-value
                count = struct.unpack_from("<I", data, pos)[0]
                pos += 4
                values = []
                for _ in range(min(count, (size - pos) // 8 + 1)):
                    value, pos = _read_prop_value(data, pos, prop_type)
                    pos = (pos + 7) & ~7
                    values.append(value)
                props[tag] = values
            else:
                value, pos = _read_prop_value(data, pos, prop_type)
                pos = (pos + 7) & ~7
                props[tag] = value
        except struct.error:
            break  # Truncated value; stop parsing the remainder.
    return props


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    text = _HTML_TAG_RE.sub(" ", html)
    return re.sub(r"[ \t]+", " ", text)


class MsgReader:
    """Open a .msg file and render a readable text preview."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.storage = _OleStorage(self.path)
        self.top_streams = {
            str(entry["name"]): self.storage.stream(entry)
            for entry in self.storage.dir_entries
            if entry["type"] == 2
        }
        self.top_props = _parse_property_stream(self.top_streams.get("__properties_version1.0", b""))
        self.recipients: list[dict[str, object]] = []
        for name in sorted(str(e["name"]) for e in self.storage.dir_entries
                           if str(e["name"]).startswith("__recip_version1.0")):
            streams = self.storage.child_streams(name)
            props = _parse_property_stream(streams.get("__properties_version1.0", b""))
            merged: dict[int, object] = dict(props)
            for stream_name, raw in streams.items():
                match = re.match(r"^__substg1\.0_([0-9A-F]{4})([0-9A-F]{4})$", stream_name)
                if not match:
                    continue
                tag = int(match.group(1), 16)
                prop_type = match.group(2)
                if tag not in merged:
                    if prop_type == "001F":
                        merged[tag] = raw.decode("utf-16le", errors="replace").rstrip("\x00")
                    elif prop_type == "001E":
                        merged[tag] = _decode_ansi(raw)
                    else:
                        merged[tag] = raw
            if merged:
                self.recipients.append(merged)
        self.attachments: list[dict[str, object]] = []
        for name in sorted(str(e["name"]) for e in self.storage.dir_entries
                           if str(e["name"]).startswith("__attach_version1.0")):
            streams = self.storage.child_streams(name)
            props = _parse_property_stream(streams.get("__properties_version1.0", b""))
            merged = dict(props)
            for stream_name, raw in streams.items():
                match = re.match(r"^__substg1\.0_([0-9A-F]{4})([0-9A-F]{4})$", stream_name)
                if not match:
                    continue
                tag = int(match.group(1), 16)
                prop_type = match.group(2)
                if tag not in merged:
                    if prop_type == "001F":
                        merged[tag] = raw.decode("utf-16le", errors="replace").rstrip("\x00")
                    elif prop_type == "001E":
                        merged[tag] = _decode_ansi(raw)
                    else:
                        merged[tag] = raw
            self.attachments.append(merged)

    def _prop(self, tag: int, fallback_tags: tuple[int, ...] = ()) -> object:
        if tag in self.top_props:
            return self.top_props[tag]
        for fallback in fallback_tags:
            if fallback in self.top_props:
                return self.top_props[fallback]
        return None

    def _stream_prop(self, tags: tuple[int, ...]) -> object:
        for tag in tags:
            for suffix in ("001F", "001E"):
                raw = self.top_streams.get("__substg1.0_%04X%s" % (tag, suffix))
                if raw is not None:
                    return raw.decode("utf-16le", errors="replace").rstrip("\x00") if suffix == "001F" else _decode_ansi(raw)
        return None

    @staticmethod
    def _as_text(value: object) -> str:
        if isinstance(value, str):
            return value
        if isinstance(value, bytes):
            return ""
        if isinstance(value, list):
            return "; ".join(str(item) for item in value if item not in (None, b""))
        return "" if value is None else str(value)

    def preview_text(self) -> str:
        subject = self._prop(0x0037) or self._stream_prop((0x0037,))
        sender_name = self._prop(0x0C1A, (0x0042,)) or self._stream_prop((0x0C1A, 0x0042))
        sender_email = self._prop(0x3FFA, (0x800A, 0x0C1F)) or self._stream_prop((0x3FFA, 0x800A))
        sent = self._prop(0x0039) or self._stream_prop((0x0039,))
        display_to = self._prop(0x0E03)
        display_cc = self._prop(0x0E04)

        to_names: list[str] = []
        cc_names: list[str] = []
        for recipient in self.recipients:
            recipient_type = int(recipient.get(0x0C15, 1) or 1)
            name = self._as_text(recipient.get(0x3001) or recipient.get(0x39FE) or recipient.get(0x3003))
            email = self._as_text(recipient.get(0x39FE) or recipient.get(0x6001) or recipient.get(0x3003))
            label = f"{name} <{email}>" if email and email not in name else (name or email)
            if not label:
                continue
            if recipient_type == 2:
                cc_names.append(label)
            elif recipient_type == 3:
                cc_names.append(label)  # Bcc is shown with Cc for readability.
            else:
                to_names.append(label)
        if not to_names and display_to:
            to_names = [part.strip() for part in str(display_to).split(";") if part.strip()]
        if not cc_names and display_cc:
            cc_names = [part.strip() for part in str(display_cc).split(";") if part.strip()]

        lines: list[str] = []
        lines.append(f"Subject: {subject or '(no subject)'}")
        sender_label = sender_name or sender_email
        if sender_label:
            lines.append(f"From: {sender_label}" + (f" <{sender_email}>" if sender_email and "@" in str(sender_email) else ""))
        if to_names:
            lines.append("To: " + "; ".join(to_names[:24]))
        if cc_names:
            lines.append("Cc: " + "; ".join(cc_names[:24]))
        if sent:
            lines.append(f"Date: {sent}")
        if self.attachments:
            attach_names = [
                self._as_text(attachment.get(0x3707) or attachment.get(0x3704))
                for attachment in self.attachments
            ]
            attach_names = [name for name in attach_names if name]
            if attach_names:
                lines.append(f"Attachments ({len(attach_names)}): " + "; ".join(attach_names[:20]))

        body = self._prop(0x1000) or self._stream_prop((0x1000, 0x1001))
        if not body:
            html = self._prop(0x1013) or self.top_streams.get("__substg1.0_10130102")
            if html:
                body = _strip_html(self._as_text(html))
        body = self._as_text(body)
        if body:
            lines.extend(["", body.rstrip()])
        else:
            rtf = self._prop(0x1009) or self.top_streams.get("__substg1.0_10090102")
            if rtf:
                lines.extend(["", "(message body is RTF-formatted; open in Outlook for full fidelity)"])
            else:
                lines.extend(["", "(no readable body in this message)"])
        return "\n".join(lines)


if __name__ == "__main__":
    import sys
    for path in sys.argv[1:]:
        try:
            print(MsgReader(path).preview_text())
        except Exception as exc:  # pragma: no cover - CLI diagnostic path
            print(f"Preview unavailable: {exc}")
