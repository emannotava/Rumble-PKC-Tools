#!/usr/bin/env python3
import argparse
import copy
import json
import os
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
	sys.path.insert(0, SCRIPT_DIR)

try:
	from pyPKCToolBase import decode_pkc, encode_pkc_document
except ImportError as exc:
	print(f"Failed to import pyPKCToolBase.py: {exc}", file=sys.stderr)
	sys.exit(1)

FORMAT_VERSION = "cpiipersonaldata-init-semantic-json-1"
SCAN_START_PC = 0x0600
SCAN_END_PC = 0x0BFF


def dump_json(path, document):
	with open(path, "w", encoding="utf-8", newline="\n") as outfile:
		outfile.write(json.dumps(document, ensure_ascii=False, indent="\t"))
		outfile.write("\n")


def load_json(path):
	with open(path, "r", encoding="utf-8") as infile:
		return json.load(infile)


def basename_without_ext(path):
	return os.path.splitext(os.path.basename(path))[0]


def default_json_output(input_path):
	return basename_without_ext(input_path) + ".json"


def default_pkc_output(original_pkc_path):
	base, ext = os.path.splitext(original_pkc_path)
	return base + ".rebuilt" + ext


def signed24_to_u24(value):
	integer = int(value)
	if not -(1 << 23) <= integer < (1 << 23):
		raise ValueError(f"Value out of signed 24-bit range: {integer}")
	return integer & 0xFFFFFF


def build_pc_maps(document):
	instructions = document["code_section"]["instructions"]
	pc_to_instruction = {}
	for index, instruction in enumerate(instructions):
		pc = int(instruction["pc_units"])
		instruction["_scan_index"] = index
		pc_to_instruction[pc] = instruction
	return instructions, pc_to_instruction


def expect_instruction(pc_to_instruction, pc, opname):
	instruction = pc_to_instruction.get(pc)
	if instruction is None:
		raise ValueError(f"Expected instruction at PC 0x{pc:04X}, but none was found")
	if instruction["opname"] != opname:
		raise ValueError(f"Expected {opname} at PC 0x{pc:04X}, found {instruction['opname']}")
	return instruction


def read_push_int(pc_to_instruction, pc):
	instruction = expect_instruction(pc_to_instruction, pc, "push_int")
	return int(instruction.get("decoded", {}).get("value_signed"))


def write_push_int(pc_to_instruction, pc, value):
	instruction = expect_instruction(pc_to_instruction, pc, "push_int")
	instruction["imm"] = signed24_to_u24(value)
	instruction["imm_hex"] = f"0x{instruction['imm']:06X}"
	instruction["decoded"] = {
		"type": "int24",
		"value_signed": int(value),
		"value_unsigned": instruction["imm"],
	}


def read_push_float(pc_to_instruction, pc):
	instruction = expect_instruction(pc_to_instruction, pc, "push_s_f")
	return float(instruction.get("decoded", {}).get("value"))


def write_push_float(pc_to_instruction, pc, value):
	instruction = expect_instruction(pc_to_instruction, pc, "push_s_f")
	import struct
	float_value = float(value)
	instruction["payload_hex"] = struct.pack(">f", float_value).hex().upper()
	instruction["decoded"] = {
		"type": "float",
		"value": float_value,
		"source_text": repr(float_value),
		"bit_pattern_hex": instruction["payload_hex"],
	}


def scan_catgets_message_ids(instructions):
	rows = []
	for index, instruction in enumerate(instructions):
		decoded = instruction.get("decoded", {})
		if decoded.get("type") != "external_call" or decoded.get("function_name") != "CatGets":
			continue
		if index < 2:
			continue
		push_dat = instructions[index - 1]
		push_int = instructions[index - 2]
		if push_dat["opname"] != "push_dat" or push_int["opname"] != "push_int":
			continue
		pc = int(push_int["pc_units"])
		rows.append({
			"pc": f"0x{pc:04X}",
			"message_id": int(push_int["decoded"]["value_signed"]),
		})
	return rows


def scan_waza_desc_field_indexes(instructions):
	rows = []
	for index, instruction in enumerate(instructions):
		decoded = instruction.get("decoded", {})
		if decoded.get("type") != "external_call" or decoded.get("function_name") != "GetWazaDesc":
			continue
		if index < 1 or instructions[index - 1]["opname"] != "push_int":
			continue
		field_instruction = instructions[index - 1]
		rows.append({
			"pc": f"0x{int(field_instruction['pc_units']):04X}",
			"field_index": int(field_instruction["decoded"]["value_signed"]),
		})
	return rows


def scan_ppr_get_field_indexes(instructions):
	rows = []
	for index, instruction in enumerate(instructions):
		decoded = instruction.get("decoded", {})
		if decoded.get("type") != "external_call" or decoded.get("function_name") != "method_pprGet":
			continue
		if index < 1 or instructions[index - 1]["opname"] != "push_int":
			continue
		field_instruction = instructions[index - 1]
		rows.append({
			"pc": f"0x{int(field_instruction['pc_units']):04X}",
			"field_index": int(field_instruction["decoded"]["value_signed"]),
		})
	return rows


def scan_waza_level_sections(instructions):
	rows = []
	for index, instruction in enumerate(instructions):
		decoded = instruction.get("decoded", {})
		if decoded.get("type") != "external_call" or decoded.get("function_name") != "method_pprGetWazaLevel":
			continue
		if index < 2:
			continue
		segment_instruction = instructions[index - 2]
		section_instruction = instructions[index - 1]
		if segment_instruction["opname"] != "push_int" or section_instruction["opname"] != "push_int":
			continue
		rows.append({
			"pc_segment": f"0x{int(segment_instruction['pc_units']):04X}",
			"segment": int(segment_instruction["decoded"]["value_signed"]),
			"pc_section": f"0x{int(section_instruction['pc_units']):04X}",
			"section_id": int(section_instruction["decoded"]["value_signed"]),
		})
	return rows


def scan_scalar_constants(instructions):
	rows = []
	for instruction in instructions:
		pc = int(instruction["pc_units"])
		if pc < SCAN_START_PC or pc > SCAN_END_PC:
			continue
		if instruction["opname"] != "push_s_f":
			continue
		rows.append({
			"pc": f"0x{pc:04X}",
			"value": float(instruction["decoded"]["value"]),
		})
	return rows


def scan_integer_constants(instructions, excluded_pcs):
	rows = []
	for instruction in instructions:
		pc = int(instruction["pc_units"])
		if pc < SCAN_START_PC or pc > SCAN_END_PC:
			continue
		if instruction["opname"] != "push_int":
			continue
		if pc in excluded_pcs:
			continue
		rows.append({
			"pc": f"0x{pc:04X}",
			"value": int(instruction["decoded"]["value_signed"]),
		})
	return rows


def export_semantic(document):
	instructions, pc_to_instruction = build_pc_maps(document)
	message_ids = scan_catgets_message_ids(instructions)
	waza_desc = scan_waza_desc_field_indexes(instructions)
	ppr_get = scan_ppr_get_field_indexes(instructions)
	waza_level = scan_waza_level_sections(instructions)
	excluded = {int(item["pc"], 16) for item in message_ids}
	excluded.update(int(item["pc"], 16) for item in waza_desc)
	excluded.update(int(item["pc"], 16) for item in ppr_get)
	excluded.update(int(item["pc_segment"], 16) for item in waza_level)
	excluded.update(int(item["pc_section"], 16) for item in waza_level)
	return {
		"format": FORMAT_VERSION,
		"source_basename": document.get("source_basename"),
		"message_ids": message_ids,
		"field_indexes": {
			"waza_desc": waza_desc,
			"ppr_get": ppr_get,
			"waza_level_sections": waza_level,
		},
		"scalar_constants": scan_scalar_constants(instructions),
		"integer_constants": scan_integer_constants(instructions, excluded),
	}


def apply_semantic(document, semantic):
	if semantic.get("format") != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format: {semantic.get('format')!r}")
	instructions, pc_to_instruction = build_pc_maps(document)
	for row in semantic.get("message_ids", []):
		write_push_int(pc_to_instruction, int(str(row["pc"]), 16), row["message_id"])
	for row in semantic["field_indexes"]["waza_desc"]:
		write_push_int(pc_to_instruction, int(str(row["pc"]), 16), row["field_index"])
	for row in semantic["field_indexes"]["ppr_get"]:
		write_push_int(pc_to_instruction, int(str(row["pc"]), 16), row["field_index"])
	for row in semantic["field_indexes"]["waza_level_sections"]:
		write_push_int(pc_to_instruction, int(str(row["pc_segment"]), 16), row["segment"])
		write_push_int(pc_to_instruction, int(str(row["pc_section"]), 16), row["section_id"])
	for row in semantic.get("scalar_constants", []):
		write_push_float(pc_to_instruction, int(str(row["pc"]), 16), row["value"])
	for row in semantic.get("integer_constants", []):
		write_push_int(pc_to_instruction, int(str(row["pc"]), 16), row["value"])
	return document


def command_export(args):
	document = decode_pkc(args.input)
	semantic = export_semantic(document)
	output = args.output or default_json_output(args.input)
	dump_json(output, semantic)
	print(f"Wrote {output}")


def command_import(args):
	semantic = load_json(args.input)
	document = decode_pkc(args.original_pkc)
	document = apply_semantic(document, semantic)
	output = args.output or default_pkc_output(args.original_pkc)
	with open(output, "wb") as outfile:
		outfile.write(encode_pkc_document(document))
	print(f"Wrote {output}")


def command_verify(args):
	document = decode_pkc(args.input)
	semantic = export_semantic(document)
	rebuilt_document = apply_semantic(copy.deepcopy(document), semantic)
	rebuilt = encode_pkc_document(rebuilt_document)
	with open(args.input, "rb") as infile:
		original = infile.read()
	if rebuilt != original:
		print("Roundtrip FAILED", file=sys.stderr)
		for index, (left, right) in enumerate(zip(original, rebuilt)):
			if left != right:
				print(f"First difference at 0x{index:X}: original=0x{left:02X}, rebuilt=0x{right:02X}", file=sys.stderr)
				break
		if len(original) != len(rebuilt):
			print(f"Length differs: original={len(original)} rebuilt={len(rebuilt)}", file=sys.stderr)
		raise SystemExit(1)
	print("Roundtrip OK")


def build_arg_parser():
	parser = argparse.ArgumentParser(description="Edit CPiiPersonalData_init.pkc via grouped semantic JSON.")
	subparsers = parser.add_subparsers(dest="command", required=True)
	export_parser = subparsers.add_parser("export", help="Export a PKC to editable JSON.")
	export_parser.add_argument("input", help="Input PKC file")
	export_parser.add_argument("output", nargs="?", help="Output JSON file")
	export_parser.set_defaults(func=command_export)
	import_parser = subparsers.add_parser("import", help="Rebuild a PKC from exported JSON.")
	import_parser.add_argument("input", help="Input JSON file")
	import_parser.add_argument("original_pkc", help="Original PKC file used for default rebuilt naming")
	import_parser.add_argument("output", nargs="?", help="Output PKC file")
	import_parser.set_defaults(func=command_import)
	verify_parser = subparsers.add_parser("verify", help="Decode and immediately rebuild a PKC, then compare bytes.")
	verify_parser.add_argument("input", help="Input PKC file")
	verify_parser.set_defaults(func=command_verify)
	return parser


def main():
	parser = build_arg_parser()
	args = parser.parse_args()
	args.func(args)


if __name__ == "__main__":
	main()
