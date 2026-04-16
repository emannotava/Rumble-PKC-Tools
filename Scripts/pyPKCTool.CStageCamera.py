#!/usr/bin/env python3
import argparse
import json
import os
import sys

try:
	from pyPKCToolBase import decode_pkc, encode_pkc_document
except ImportError as exc:
	print(f"Failed to import pyPKCToolBase.py: {exc}", file=sys.stderr)
	sys.exit(1)


TOOL_DESCRIPTION = "Edit CStageCamera.pkc via semantic JSON."
FORMAT_VERSION = "cstagecamera-semantic-json-1"

COMMON_NEWITEM_STRING_PCS = [
	0x0394, 0x039A, 0x03A0, 0x03A6, 0x03AC, 0x03B2, 0x03B8, 0x03BE,
	0x03C4, 0x03CA, 0x03D0, 0x03D6, 0x03DC, 0x03E2, 0x03E8, 0x03EE,
	0x040A, 0x075F,
]

COMMON_MESSAGE_PC_MAP = {
	"dynamic_category_base_high": 0x056A,
	"dynamic_category_base_low": 0x0575,
	"difficulty_rank_labels": [0x0858, 0x085D, 0x0862, 0x086E, 0x0873, 0x0878, 0x087D, 0x0882],
}

CAMERA_PCS = {
	"rotate_x_magnitude": 0x08FE,
	"rotate_y_deg": 0x0901,
	"rotate_z_deg": 0x0903,
	"fovy": 0x0908,
	"near": 0x090D,
	"far": 0x0912,
}

TARGET_PCS = {
	"nearest_obj_mask": 0x0919,
	"dummy_target_script": 0x091C,
	"show_dummy_target": 0x0922,
}


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
	pc_to_instruction = {}
	pc_to_string_index = {}
	for instruction in document["code_section"]["instructions"]:
		pc = int(instruction["pc_units"])
		pc_to_instruction[pc] = instruction
		if instruction["opname"] == "push_str":
			decoded = instruction.get("decoded", {})
			pc_to_string_index[pc] = int(decoded["index"])
	return pc_to_instruction, pc_to_string_index


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
	instruction["decoded"] = {"type": "int24", "value_signed": int(value), "value_unsigned": instruction["imm"]}


def read_push_float(pc_to_instruction, pc):
	instruction = expect_instruction(pc_to_instruction, pc, "push_s_f")
	return float(instruction.get("decoded", {}).get("value"))


def write_push_float(pc_to_instruction, pc, value):
	instruction = expect_instruction(pc_to_instruction, pc, "push_s_f")
	float_value = float(value)
	instruction["decoded"] = {"type": "float", "value": float_value, "source_text": repr(float_value), "bit_pattern_hex": None}


def read_push_str(string_entries, pc_to_string_index, pc):
	if pc not in pc_to_string_index:
		raise ValueError(f"Expected push_str at PC 0x{pc:04X}, but none was found")
	index = pc_to_string_index[pc]
	return string_entries[index], index


def write_push_str_group(string_entries, pc_to_string_index, pcs, values):
	index_to_value = {}
	for pc, value in zip(pcs, values):
		if pc not in pc_to_string_index:
			raise ValueError(f"Expected push_str at PC 0x{pc:04X}, but none was found")
		index = pc_to_string_index[pc]
		if index in index_to_value and index_to_value[index] != value:
			raise ValueError(f"Conflicting values for shared string index {index}: {index_to_value[index]!r} vs {value!r}")
		index_to_value[index] = value
	for index, value in index_to_value.items():
		string_entries[index] = str(value)


def read_string_list(string_entries, pc_to_string_index, pcs):
	return [read_push_str(string_entries, pc_to_string_index, pc)[0] for pc in pcs]


def read_int_list(pc_to_instruction, pcs):
	return [read_push_int(pc_to_instruction, pc) for pc in pcs]


def write_int_list(pc_to_instruction, pcs, values, label):
	if len(values) != len(pcs):
		raise ValueError(f"{label} must contain {len(pcs)} values, got {len(values)}")
	for pc, value in zip(pcs, values):
		write_push_int(pc_to_instruction, pc, value)


def export_semantic(document):
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	return {
		"format": FORMAT_VERSION,
		"source_basename": document.get("source_basename"),
		"embedded_scripts": {
			"newitem_sequence": read_string_list(string_entries, pc_to_string_index, COMMON_NEWITEM_STRING_PCS),
			"dummy_target_script": read_push_str(string_entries, pc_to_string_index, TARGET_PCS["dummy_target_script"])[0],
		},
		"common_messages": {
			"dynamic_category_base_high": read_push_int(pc_to_instruction, COMMON_MESSAGE_PC_MAP["dynamic_category_base_high"]),
			"dynamic_category_base_low": read_push_int(pc_to_instruction, COMMON_MESSAGE_PC_MAP["dynamic_category_base_low"]),
			"difficulty_rank_labels": read_int_list(pc_to_instruction, COMMON_MESSAGE_PC_MAP["difficulty_rank_labels"]),
		},
		"camera_setup": {
			"rotate_x_deg": -abs(read_push_float(pc_to_instruction, CAMERA_PCS["rotate_x_magnitude"])),
			"rotate_y_deg": read_push_float(pc_to_instruction, CAMERA_PCS["rotate_y_deg"]),
			"rotate_z_deg": read_push_float(pc_to_instruction, CAMERA_PCS["rotate_z_deg"]),
			"fovy": read_push_float(pc_to_instruction, CAMERA_PCS["fovy"]),
			"near": read_push_float(pc_to_instruction, CAMERA_PCS["near"]),
			"far": read_push_float(pc_to_instruction, CAMERA_PCS["far"]),
		},
		"target_tracking": {
			"nearest_obj_mask": read_push_int(pc_to_instruction, TARGET_PCS["nearest_obj_mask"]),
			"show_dummy_target": read_push_int(pc_to_instruction, TARGET_PCS["show_dummy_target"]),
		},
	}


def apply_semantic(document, semantic):
	if semantic.get("format") != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format: {semantic.get('format')!r}")
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	write_push_str_group(string_entries, pc_to_string_index, COMMON_NEWITEM_STRING_PCS, semantic["embedded_scripts"]["newitem_sequence"])
	write_push_str_group(string_entries, pc_to_string_index, [TARGET_PCS["dummy_target_script"]], [semantic["embedded_scripts"]["dummy_target_script"]])
	write_push_int(pc_to_instruction, COMMON_MESSAGE_PC_MAP["dynamic_category_base_high"], semantic["common_messages"]["dynamic_category_base_high"])
	write_push_int(pc_to_instruction, COMMON_MESSAGE_PC_MAP["dynamic_category_base_low"], semantic["common_messages"]["dynamic_category_base_low"])
	write_int_list(pc_to_instruction, COMMON_MESSAGE_PC_MAP["difficulty_rank_labels"], semantic["common_messages"]["difficulty_rank_labels"], "common_messages.difficulty_rank_labels")
	rotate_x = float(semantic["camera_setup"]["rotate_x_deg"])
	if rotate_x > 0:
		raise ValueError("camera_setup.rotate_x_deg must be zero or negative because the script hardcodes a negate opcode")
	write_push_float(pc_to_instruction, CAMERA_PCS["rotate_x_magnitude"], abs(rotate_x))
	write_push_float(pc_to_instruction, CAMERA_PCS["rotate_y_deg"], semantic["camera_setup"]["rotate_y_deg"])
	write_push_float(pc_to_instruction, CAMERA_PCS["rotate_z_deg"], semantic["camera_setup"]["rotate_z_deg"])
	write_push_float(pc_to_instruction, CAMERA_PCS["fovy"], semantic["camera_setup"]["fovy"])
	write_push_float(pc_to_instruction, CAMERA_PCS["near"], semantic["camera_setup"]["near"])
	write_push_float(pc_to_instruction, CAMERA_PCS["far"], semantic["camera_setup"]["far"])
	write_push_int(pc_to_instruction, TARGET_PCS["nearest_obj_mask"], semantic["target_tracking"]["nearest_obj_mask"])
	write_push_int(pc_to_instruction, TARGET_PCS["show_dummy_target"], semantic["target_tracking"]["show_dummy_target"])
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
	rebuilt_document = apply_semantic(decode_pkc(args.input), semantic)
	rebuilt_bytes = encode_pkc_document(rebuilt_document)
	with open(args.input, "rb") as infile:
		original_bytes = infile.read()
	if rebuilt_bytes != original_bytes:
		print("Roundtrip FAILED", file=sys.stderr)
		for index, (left, right) in enumerate(zip(original_bytes, rebuilt_bytes)):
			if left != right:
				print(f"First difference at 0x{index:X}: original=0x{left:02X}, rebuilt=0x{right:02X}", file=sys.stderr)
				break
		if len(original_bytes) != len(rebuilt_bytes):
			print(f"Length differs: original={len(original_bytes)} rebuilt={len(rebuilt_bytes)}", file=sys.stderr)
		raise SystemExit(1)
	print("Roundtrip OK")


def build_arg_parser():
	parser = argparse.ArgumentParser(description=TOOL_DESCRIPTION)
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
