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

FORMAT_VERSION = "cstatistic-semantic-json-1"
SETTER_NAME = "SetStatCounterStopValue"


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
	for index, instruction in enumerate(instructions):
		instruction["_scan_index"] = index
	return instructions


def parse_rows(document):
	instructions = build_pc_maps(document)
	rows = []
	index = 0
	while index < len(instructions):
		instruction = instructions[index]
		if instruction["opname"] != "push_int":
			index += 1
			continue
		if index + 3 < len(instructions):
			call_instruction = instructions[index + 2]
			pop_instruction = instructions[index + 3]
			if instructions[index + 1]["opname"] == "push_int" and call_instruction.get("decoded", {}).get("function_name") == SETTER_NAME and pop_instruction["opname"] == "pop":
				rows.append({
					"row_index": len(rows),
					"pc_call": f"0x{int(call_instruction['pc_units']):04X}",
					"pc_stat_id": f"0x{int(instruction['pc_units']):04X}",
					"stat_id": int(instruction["decoded"]["value_signed"]),
					"encoding": "push_int",
					"pc_value": f"0x{int(instructions[index + 1]['pc_units']):04X}",
					"stop_value": int(instructions[index + 1]["decoded"]["value_signed"]),
				})
				index += 4
				continue
		if index + 4 < len(instructions):
			legacy_opcode = instructions[index + 1]
			value_instruction = instructions[index + 2]
			call_instruction = instructions[index + 3]
			pop_instruction = instructions[index + 4]
			if legacy_opcode["opcode"] == 0x3F and call_instruction.get("decoded", {}).get("function_name") == SETTER_NAME and pop_instruction["opname"] == "pop":
				rows.append({
					"row_index": len(rows),
					"pc_call": f"0x{int(call_instruction['pc_units']):04X}",
					"pc_stat_id": f"0x{int(instruction['pc_units']):04X}",
					"stat_id": int(instruction["decoded"]["value_signed"]),
					"encoding": f"legacy_opcode_0x{int(value_instruction['opcode']):02X}",
					"pc_value": f"0x{int(value_instruction['pc_units']):04X}",
					"stop_value": int(value_instruction["imm"]),
				})
				index += 5
				continue
		index += 1
	return rows


def export_semantic(document):
	return {
		"format": FORMAT_VERSION,
		"source_basename": document.get("source_basename"),
		"rows": parse_rows(document),
	}


def apply_semantic(document, semantic):
	if semantic.get("format") != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format: {semantic.get('format')!r}")
	instructions = build_pc_maps(document)
	rows = parse_rows(document)
	if len(rows) != len(semantic.get("rows", [])):
		raise ValueError(f"rows length changed from {len(rows)} to {len(semantic.get('rows', []))}")
	for original_row, edited_row in zip(rows, semantic["rows"]):
		stat_instruction = None
		for instruction in instructions:
			if int(instruction["pc_units"]) == int(original_row["pc_stat_id"], 16):
				stat_instruction = instruction
				break
		if stat_instruction is None:
			raise ValueError(f"Could not resolve stat row PC {original_row['pc_stat_id']}")
		stat_instruction["imm"] = signed24_to_u24(edited_row["stat_id"])
		stat_instruction["imm_hex"] = f"0x{stat_instruction['imm']:06X}"
		stat_instruction["decoded"] = {
			"type": "int24",
			"value_signed": int(edited_row["stat_id"]),
			"value_unsigned": stat_instruction["imm"],
		}
		value_instruction = None
		for instruction in instructions:
			if int(instruction["pc_units"]) == int(original_row["pc_value"], 16):
				value_instruction = instruction
				break
		if value_instruction is None:
			raise ValueError(f"Could not resolve stop-value PC {original_row['pc_value']}")
		value = int(edited_row["stop_value"])
		if original_row["encoding"] == "push_int":
			value_instruction["opcode"] = 0x40
			value_instruction["opname"] = "push_int"
			value_instruction["imm"] = signed24_to_u24(value)
			value_instruction["imm_hex"] = f"0x{value_instruction['imm']:06X}"
			value_instruction["decoded"] = {
				"type": "int24",
				"value_signed": value,
				"value_unsigned": value_instruction["imm"],
			}
		else:
			# Preserve the legacy opcode and rewrite the immediate as an unsigned 24-bit value.
			value_instruction["imm"] = int(value) & 0xFFFFFF
			value_instruction["imm_hex"] = f"0x{value_instruction['imm']:06X}"
			value_instruction.pop("decoded", None)
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
	parser = argparse.ArgumentParser(description="Edit CStatistic.pkc as a stat stop-value table.")
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
