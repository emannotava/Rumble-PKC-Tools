#!/usr/bin/env python3
import argparse
import json
import os
import struct
import sys
from enum import IntEnum

try:
	from script_functions import script_functions
except ImportError as exc:
	print(f"Failed to import script_functions.py: {exc}", file=sys.stderr)
	sys.exit(1)


FORMAT_VERSION = "pkc-json-1"


class PKCOpCode(IntEnum):
	nop_0 = 0x00
	nop_1 = 0x02
	negate = 0x03
	complement = 0x04
	add = 0x05
	sub = 0x06
	multiply = 0x07
	divide = 0x08
	store = 0x0A
	cmp_eq = 0x0B
	cmp_neq = 0x0C
	cmp_lt = 0x0D
	cmp_geq = 0x0E
	cmp_leq = 0x0F
	cmp_gt = 0x10
	is_zero = 0x11
	bitwise_and = 0x14
	bitwise_xor = 0x15
	bitwise_or = 0x16
	bitwise_rshift = 0x17
	bitwise_lshift = 0x18
	access = 0x19
	call_imm = 0x1A
	call_ext_0 = 0x1B
	jmp_imm = 0x1C
	jmp_if_false = 0x1D
	function_epilogue = 0x1E
	pop = 0x1F
	function_prologue = 0x20
	store_ptr = 0x22
	exit = 0x23
	jmp_if_true = 0x24
	jmp_if_false_c = 0x25
	jmp_if_true_c = 0x26
	call_ext_1 = 0x28
	peek_nop = 0x36
	access_ptr = 0x3C
	push_int = 0x40
	push_s_f = 0x41
	push_str = 0x42
	push_dat = 0x43
	nop_2 = 0x4A
	switch = 0x4C


VARARGS_SENTINEL = 0xFFFFFFFF
VARIABLE_PAYLOAD_OPCODES = {
	PKCOpCode.push_s_f.value,
	PKCOpCode.function_prologue.value,
	PKCOpCode.switch.value,
}


def opcode_name(opcode):
	try:
		return PKCOpCode(opcode).name
	except ValueError:
		return f"opcode_0x{opcode:02X}"


def sign24(value):
	return value - 0x1000000 if value & 0x800000 else value


def hex_bytes(data):
	return data.hex().upper()


def decode_ascii_cstring_pool(raw, expected_count):
	if not raw:
		return []
	parts = raw.split(b"\x00")
	if parts[-1] != b"":
		return None
	entries = []
	for part in parts[:-1]:
		try:
			entries.append(part.decode("ascii"))
		except UnicodeDecodeError:
			return None
	if expected_count is not None and expected_count != len(entries):
		return None
	return entries


def encode_ascii_cstring_pool(entries):
	out = bytearray()
	for entry in entries:
		out.extend(entry.encode("ascii"))
		out.append(0)
	return bytes(out)


def decode_utf16be_cstring_pool(raw, expected_count):
	if not raw:
		return []
	if len(raw) % 2 != 0:
		return None
	entries = []
	current = bytearray()
	for index in range(0, len(raw), 2):
		word = raw[index:index + 2]
		if word == b"\x00\x00":
			try:
				entries.append(current.decode("utf-16-be"))
			except UnicodeDecodeError:
				return None
			current = bytearray()
		else:
			current.extend(word)
	if current:
		try:
			entries.append(current.decode("utf-16-be"))
		except UnicodeDecodeError:
			return None
	if expected_count is not None and expected_count != len(entries):
		return None
	return entries


def encode_utf16be_cstring_pool(entries):
	out = bytearray()
	for entry in entries:
		out.extend(entry.encode("utf-16-be"))
		out.extend(b"\x00\x00")
	return bytes(out)


def decode_data_section(raw, expected_count):
	if not raw:
		return {
			"encoding": "raw",
			"entries": [],
			"raw_hex": "",
			"count": expected_count or 0,
		}

	utf16_entries = decode_utf16be_cstring_pool(raw, expected_count)
	if utf16_entries is not None:
		return {
			"encoding": "utf16be_cstring_pool",
			"entries": utf16_entries,
			"raw_hex": hex_bytes(raw),
			"count": len(utf16_entries),
		}

	return {
		"encoding": "raw",
		"entries": [],
		"raw_hex": hex_bytes(raw),
		"count": expected_count or 0,
	}


def encode_data_section(section):
	encoding = section.get("encoding", "raw")
	if encoding == "utf16be_cstring_pool":
		entries = section.get("entries", [])
		return encode_utf16be_cstring_pool(entries), len(entries)
	if encoding == "raw":
		raw_hex = section.get("raw_hex", "")
		return bytes.fromhex(raw_hex), int(section.get("count", 0))
	raise ValueError(f"Unsupported data_section encoding: {encoding}")


def read_pkc(path):
	with open(path, "rb") as infile:
		raw = infile.read()

	if len(raw) < 0x20:
		raise ValueError("File is too small to be a PKC")

	magic = raw[0:4]
	if magic not in (b"pk\x1A\x20", b"\x20\x1Akp", b"PK\x1A\x20", b"\x20\x1AKP"):
		raise ValueError(f"Unsupported PKC magic: {magic!r}")

	flags, code_size, data_size, data_count, string_size, string_count, reserved = struct.unpack(">7I", raw[4:0x20])

	code_start = 0x20
	data_start = code_start + code_size
	string_start = data_start + data_size
	trailing_start = string_start + string_size

	if trailing_start > len(raw):
		raise ValueError("Section sizes run past end of file")

	return {
		"path": path,
		"basename": os.path.basename(path),
		"magic_hex": hex_bytes(magic),
		"flags": flags,
		"code_size": code_size,
		"data_size": data_size,
		"data_count": data_count,
		"string_size": string_size,
		"string_count": string_count,
		"reserved": reserved,
		"code_raw": raw[code_start:data_start],
		"data_raw": raw[data_start:string_start],
		"string_raw": raw[string_start:trailing_start],
		"trailing_raw": raw[trailing_start:],
	}


def decode_instruction_stream(code_raw, string_entries, data_section):
	instructions = []
	byte_offset = 0
	pc_units = 0
	data_entries = data_section.get("entries", [])

	while byte_offset < len(code_raw):
		if byte_offset + 4 > len(code_raw):
			raise ValueError(f"Truncated instruction header at byte offset 0x{byte_offset:X}")

		start = byte_offset
		opcode = code_raw[byte_offset]
		imm = int.from_bytes(code_raw[byte_offset + 1:byte_offset + 4], "big")
		byte_offset += 4
		payload = b""
		length_units = 1

		if opcode == PKCOpCode.push_s_f.value:
			if byte_offset + 4 > len(code_raw):
				raise ValueError(f"Truncated push_s_f payload at byte offset 0x{byte_offset:X}")
			payload = code_raw[byte_offset:byte_offset + 4]
			byte_offset += 4
			length_units = 2
		elif opcode == PKCOpCode.function_prologue.value:
			if byte_offset + 4 > len(code_raw):
				raise ValueError(f"Truncated function_prologue payload at byte offset 0x{byte_offset:X}")
			payload = code_raw[byte_offset:byte_offset + 4]
			byte_offset += 4
			length_units = 2
		elif opcode == PKCOpCode.switch.value:
			case_count = imm >> 16
			payload_size = case_count * 2
			if case_count & 1:
				payload_size += 2
			if byte_offset + payload_size > len(code_raw):
				raise ValueError(f"Truncated switch payload at byte offset 0x{byte_offset:X}")
			payload = code_raw[byte_offset:byte_offset + payload_size]
			byte_offset += payload_size

		instruction = {
			"index": len(instructions),
			"pc_units": pc_units,
			"byte_offset": start,
			"opcode": opcode,
			"opname": opcode_name(opcode),
			"imm": imm,
			"imm_hex": f"0x{imm:06X}",
			"length_bytes": byte_offset - start,
			"length_units": length_units,
			"payload_hex": hex_bytes(payload),
		}

		if opcode == PKCOpCode.push_s_f.value:
			instruction["decoded"] = {
				"type": "float",
				"value": struct.unpack(">f", payload)[0],
			}
		elif opcode == PKCOpCode.function_prologue.value:
			payload_value = struct.unpack(">I", payload)[0]
			instruction["decoded"] = {
				"type": "function_prologue_payload",
				"value_u32": payload_value,
				"value_hex": f"0x{payload_value:08X}",
			}
		elif opcode == PKCOpCode.switch.value:
			case_count = imm >> 16
			offsets = []
			for pos in range(0, case_count * 2, 2):
				offsets.append(struct.unpack(">h", payload[pos:pos + 2])[0])
			instruction["decoded"] = {
				"type": "switch",
				"case_count": case_count,
				"case_offsets_s16": offsets,
			}
		elif opcode in (PKCOpCode.store.value, PKCOpCode.store_ptr.value, PKCOpCode.access.value, PKCOpCode.access_ptr.value):
			instruction["decoded"] = {
				"type": "frame_access",
				"segment": imm >> 16,
				"slot": imm & 0xFFFF,
			}
		elif opcode == PKCOpCode.push_int.value:
			instruction["decoded"] = {
				"type": "int24",
				"value_signed": sign24(imm),
				"value_unsigned": imm,
			}
		elif opcode == PKCOpCode.push_str.value:
			value = string_entries[imm] if 0 <= imm < len(string_entries) else None
			instruction["decoded"] = {
				"type": "string_index",
				"index": imm,
				"value": value,
			}
		elif opcode == PKCOpCode.push_dat.value:
			value = data_entries[imm] if 0 <= imm < len(data_entries) else None
			instruction["decoded"] = {
				"type": "data_index",
				"index": imm,
				"value": value,
			}
		elif opcode in (PKCOpCode.call_ext_0.value, PKCOpCode.call_ext_1.value):
			if 0 <= imm < len(script_functions):
				func_name, argc_declared = script_functions[imm]
			else:
				func_name, argc_declared = None, None
			decoded = {
				"type": "external_call",
				"function_index": imm,
				"function_name": func_name,
				"declared_argc": argc_declared,
			}
			instruction["decoded"] = decoded
		elif opcode in (
			PKCOpCode.call_imm.value,
			PKCOpCode.jmp_imm.value,
			PKCOpCode.jmp_if_false.value,
			PKCOpCode.jmp_if_true.value,
			PKCOpCode.jmp_if_false_c.value,
			PKCOpCode.jmp_if_true_c.value,
		):
			instruction["decoded"] = {
				"type": "pc_immediate",
				"value": imm,
			}

		instructions.append(instruction)
		pc_units += length_units

	for index, instruction in enumerate(instructions):
		if instruction["opcode"] == PKCOpCode.call_ext_1.value:
			next_instruction = instructions[index + 1] if index + 1 < len(instructions) else None
			if next_instruction and next_instruction["opcode"] == PKCOpCode.nop_0.value:
				instruction.setdefault("decoded", {})
				instruction["decoded"]["resolved_argc_from_next_nop_0"] = next_instruction["imm"]

	return instructions


def build_call_summary(instructions):
	call_counts = {}
	for instruction in instructions:
		if instruction["opcode"] not in (PKCOpCode.call_ext_0.value, PKCOpCode.call_ext_1.value):
			continue
		decoded = instruction.get("decoded", {})
		func_name = decoded.get("function_name") or f"<unknown_{decoded.get('function_index', instruction['imm'])}>"
		call_counts[func_name] = call_counts.get(func_name, 0) + 1
	return sorted(call_counts.items(), key=lambda item: (-item[1], item[0]))


def decode_pkc(path):
	container = read_pkc(path)

	string_entries = decode_ascii_cstring_pool(container["string_raw"], container["string_count"])
	if string_entries is None:
		string_section = {
			"encoding": "raw",
			"entries": [],
			"raw_hex": hex_bytes(container["string_raw"]),
			"count": container["string_count"],
		}
		string_entries = []
	else:
		string_section = {
			"encoding": "ascii_cstring_pool",
			"entries": string_entries,
			"raw_hex": hex_bytes(container["string_raw"]),
			"count": len(string_entries),
		}

	data_section = decode_data_section(container["data_raw"], container["data_count"])

	logical_code_raw = container["code_raw"]
	trailing_raw = container["trailing_raw"]
	code_continues_in_trailing = False

	if container["data_size"] == 0 and container["string_size"] == 0 and trailing_raw:
		logical_code_raw = container["code_raw"] + trailing_raw
		trailing_raw = b""
		code_continues_in_trailing = True

	instructions = decode_instruction_stream(logical_code_raw, string_entries, data_section)
	call_summary = build_call_summary(instructions)

	return {
		"format": FORMAT_VERSION,
		"source_basename": container["basename"],
		"header": {
			"magic_hex": container["magic_hex"],
			"flags": container["flags"],
			"reserved": container["reserved"],
		},
		"section_sizes_from_source": {
			"code_size": container["code_size"],
			"data_size": container["data_size"],
			"data_count": container["data_count"],
			"string_size": container["string_size"],
			"string_count": container["string_count"],
			"trailing_size": len(container["trailing_raw"]),
		},
		"physical_layout": {
			"code_continues_in_trailing": code_continues_in_trailing,
			"declared_code_size": container["code_size"],
		},
		"code_section": {
			"logical_code_size": len(logical_code_raw),
			"instructions": instructions,
		},
		"data_section": data_section,
		"string_section": string_section,
		"trailing_raw_hex": hex_bytes(trailing_raw),
		"summary": {
			"instruction_count": len(instructions),
			"function_prologues": sum(1 for ins in instructions if ins["opcode"] == PKCOpCode.function_prologue.value),
			"function_epilogues": sum(1 for ins in instructions if ins["opcode"] == PKCOpCode.function_epilogue.value),
			"external_call_count": sum(1 for ins in instructions if ins["opcode"] in (PKCOpCode.call_ext_0.value, PKCOpCode.call_ext_1.value)),
			"unknown_opcode_count": sum(1 for ins in instructions if ins["opcode"] not in PKCOpCode._value2member_map_),
			"top_external_calls": [{"name": name, "count": count} for name, count in call_summary[:25]],
		},
	}


def encode_instruction(instruction):
	opcode = int(instruction["opcode"])
	imm = int(instruction["imm"])
	if not 0 <= opcode <= 0xFF:
		raise ValueError(f"Opcode out of range: {opcode}")
	if not 0 <= imm <= 0xFFFFFF:
		raise ValueError(f"Immediate out of range for 24-bit field: {imm}")

	out = bytearray()
	out.append(opcode)
	out.extend(imm.to_bytes(3, "big"))

	payload_hex = instruction.get("payload_hex", "")
	payload = bytes.fromhex(payload_hex) if payload_hex else b""

	if opcode == PKCOpCode.push_s_f.value:
		if len(payload) != 4:
			raise ValueError("push_s_f payload must be exactly 4 bytes")
		out.extend(payload)
	elif opcode == PKCOpCode.function_prologue.value:
		if len(payload) != 4:
			raise ValueError("function_prologue payload must be exactly 4 bytes")
		out.extend(payload)
	elif opcode == PKCOpCode.switch.value:
		case_count = imm >> 16
		expected = case_count * 2 + (2 if case_count & 1 else 0)
		if len(payload) != expected:
			raise ValueError(
				f"switch payload length mismatch: expected {expected} bytes for {case_count} cases, got {len(payload)}"
			)
		out.extend(payload)
	else:
		if payload:
			raise ValueError(f"Opcode 0x{opcode:02X} does not use payload_hex, but payload_hex was provided")

	return bytes(out)


def encode_pkc_document(document):
	if document.get("format") != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format: {document.get('format')!r}")

	magic = bytes.fromhex(document["header"]["magic_hex"])
	if len(magic) != 4:
		raise ValueError("header.magic_hex must encode exactly 4 bytes")

	flags = int(document["header"]["flags"])
	reserved = int(document["header"].get("reserved", 0))

	code_bytes = bytearray()
	for instruction in document["code_section"]["instructions"]:
		code_bytes.extend(encode_instruction(instruction))
	code_bytes = bytes(code_bytes)

	data_bytes, data_count = encode_data_section(document["data_section"])

	string_section = document["string_section"]
	string_encoding = string_section.get("encoding", "ascii_cstring_pool")
	if string_encoding == "ascii_cstring_pool":
		string_entries = string_section.get("entries", [])
		string_bytes = encode_ascii_cstring_pool(string_entries)
		string_count = len(string_entries)
	elif string_encoding == "raw":
		string_bytes = bytes.fromhex(string_section.get("raw_hex", ""))
		string_count = int(string_section.get("count", 0))
	else:
		raise ValueError(f"Unsupported string_section encoding: {string_encoding}")

	trailing_bytes = bytes.fromhex(document.get("trailing_raw_hex", ""))
	physical_layout = document.get("physical_layout", {})
	code_continues_in_trailing = bool(physical_layout.get("code_continues_in_trailing", False))
	declared_code_size = int(physical_layout.get("declared_code_size", len(code_bytes)))

	if code_continues_in_trailing:
		if declared_code_size > len(code_bytes):
			raise ValueError(
				f"Logical code is too short to satisfy declared_code_size {declared_code_size}"
			)
		stored_code_bytes = code_bytes[:declared_code_size]
		trailing_bytes = code_bytes[declared_code_size:] + trailing_bytes
		code_size_for_header = declared_code_size
	else:
		stored_code_bytes = code_bytes
		code_size_for_header = len(stored_code_bytes)

	header = struct.pack(
		">7I",
		flags,
		code_size_for_header,
		len(data_bytes),
		data_count,
		len(string_bytes),
		string_count,
		reserved,
	)

	return magic + header + stored_code_bytes + data_bytes + string_bytes + trailing_bytes


def command_summary(args):
	document = decode_pkc(args.input)
	summary = document["summary"]
	source_sizes = document["section_sizes_from_source"]
	print(f"File: {document['source_basename']}")
	print(f"Flags: 0x{document['header']['flags']:X}")
	print(f"Reserved: 0x{document['header']['reserved']:X}")
	print(f"Code size: {source_sizes['code_size']} bytes")
	print(f"Data size/count: {source_sizes['data_size']} bytes / {source_sizes['data_count']}")
	print(f"String size/count: {source_sizes['string_size']} bytes / {source_sizes['string_count']}")
	print(f"Trailing bytes: {source_sizes['trailing_size']}")
	print(f"Instructions: {summary['instruction_count']}")
	print(f"Function prologues: {summary['function_prologues']}")
	print(f"Function epilogues: {summary['function_epilogues']}")
	print(f"External calls: {summary['external_call_count']}")
	print(f"Unknown opcodes: {summary['unknown_opcode_count']}")

	if document["data_section"]["encoding"] == "utf16be_cstring_pool":
		print("Data pool (UTF-16BE strings):")
		for index, entry in enumerate(document["data_section"]["entries"]):
			print(f"\t[{index}] {entry!r}")

	if document["string_section"]["encoding"] == "ascii_cstring_pool":
		print(f"String pool entries: {len(document['string_section']['entries'])}")
		for index, entry in enumerate(document["string_section"]["entries"][:args.max_strings]):
			print(f"\t[{index}] {entry!r}")
		if len(document["string_section"]["entries"]) > args.max_strings:
			print(f"\t... {len(document['string_section']['entries']) - args.max_strings} more")

	if args.show_calls:
		print("Top external calls:")
		for item in summary["top_external_calls"][:args.max_calls]:
			print(f"\t{item['count']:4d} {item['name']}")


def command_export(args):
	document = decode_pkc(args.input)
	output = args.output
	if output is None:
		base, _ = os.path.splitext(args.input)
		output = base + ".json"
	with open(output, "w", encoding="utf-8", newline="\n") as outfile:
		json.dump(document, outfile, indent=2, ensure_ascii=False)
		outfile.write("\n")
	print(f"Wrote {output}")


def command_import(args):
	with open(args.input, "r", encoding="utf-8") as infile:
		document = json.load(infile)
	pkc_bytes = encode_pkc_document(document)
	output = args.output
	if output is None:
		base, _ = os.path.splitext(args.input)
		output = base + ".rebuilt.pkc"
	with open(output, "wb") as outfile:
		outfile.write(pkc_bytes)
	print(f"Wrote {output}")


def command_verify(args):
	document = decode_pkc(args.input)
	rebuilt = encode_pkc_document(document)
	with open(args.input, "rb") as infile:
		original = infile.read()

	if rebuilt != original:
		print("Roundtrip FAILED", file=sys.stderr)
		print(f"Original size: {len(original)} bytes", file=sys.stderr)
		print(f"Rebuilt size:  {len(rebuilt)} bytes", file=sys.stderr)
		for index, (left, right) in enumerate(zip(original, rebuilt)):
			if left != right:
				print(
					f"First difference at 0x{index:X}: original=0x{left:02X}, rebuilt=0x{right:02X}",
					file=sys.stderr,
				)
				break
		if len(original) != len(rebuilt):
			print("Files differ in length", file=sys.stderr)
		raise SystemExit(1)

	print("Roundtrip OK")


def command_disasm(args):
	document = decode_pkc(args.input)
	instructions = document["code_section"]["instructions"]
	for instruction in instructions:
		opname = instruction["opname"]
		line = (
			f"{instruction['pc_units']:06X} "
			f"[0x{instruction['byte_offset']:06X}] "
			f"{opname:<18} imm={instruction['imm_hex']}"
		)
		decoded = instruction.get("decoded")
		if decoded:
			dtype = decoded.get("type")
			if dtype == "float":
				line += f" value={decoded['value']!r}"
			elif dtype == "int24":
				line += f" value={decoded['value_signed']}"
			elif dtype == "frame_access":
				line += f" segment={decoded['segment']} slot={decoded['slot']}"
			elif dtype == "string_index":
				line += f" str[{decoded['index']}]={decoded['value']!r}"
			elif dtype == "data_index":
				line += f" dat[{decoded['index']}]={decoded['value']!r}"
			elif dtype == "external_call":
				line += f" fn={decoded['function_name']!r} argc={decoded['declared_argc']!r}"
				if "resolved_argc_from_next_nop_0" in decoded:
					line += f" resolved_argc={decoded['resolved_argc_from_next_nop_0']}"
			elif dtype == "switch":
				line += f" cases={decoded['case_count']} offsets={decoded['case_offsets_s16']}"
			elif dtype == "function_prologue_payload":
				line += f" payload={decoded['value_hex']}"
		print(line)


def build_arg_parser():
	parser = argparse.ArgumentParser(
		description="Decode, summarize, export, import, and roundtrip-check PKC files."
	)
	subparsers = parser.add_subparsers(dest="command", required=True)

	summary_parser = subparsers.add_parser("summary", help="Show a quick structural summary of a PKC file.")
	summary_parser.add_argument("input", help="Input PKC file")
	summary_parser.add_argument("--show-calls", action="store_true", help="Show the most common external calls.")
	summary_parser.add_argument("--max-calls", type=int, default=25, help="How many calls to show when --show-calls is used.")
	summary_parser.add_argument("--max-strings", type=int, default=25, help="How many string pool entries to print.")
	summary_parser.set_defaults(func=command_summary)

	export_parser = subparsers.add_parser("export", help="Export a PKC to editable JSON.")
	export_parser.add_argument("input", help="Input PKC file")
	export_parser.add_argument("output", nargs="?", help="Output JSON file")
	export_parser.set_defaults(func=command_export)

	import_parser = subparsers.add_parser("import", help="Rebuild a PKC from exported JSON.")
	import_parser.add_argument("input", help="Input JSON file")
	import_parser.add_argument("output", nargs="?", help="Output PKC file")
	import_parser.set_defaults(func=command_import)

	verify_parser = subparsers.add_parser("verify", help="Decode and immediately rebuild a PKC, then compare bytes.")
	verify_parser.add_argument("input", help="Input PKC file")
	verify_parser.set_defaults(func=command_verify)

	disasm_parser = subparsers.add_parser("disasm", help="Print a simple instruction listing.")
	disasm_parser.add_argument("input", help="Input PKC file")
	disasm_parser.set_defaults(func=command_disasm)

	return parser


def main():
	parser = build_arg_parser()
	args = parser.parse_args()
	args.func(args)


if __name__ == "__main__":
	main()
