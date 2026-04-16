#!/usr/bin/env python3
import argparse
import copy
import json
import os
import struct
import sys

try:
	from pyPKCToolBase import decode_pkc, encode_pkc_document
except ImportError as exc:
	print(f"Failed to import pyPKCToolBase.py: {exc}", file=sys.stderr)
	sys.exit(1)


FORMAT_VERSION = "cevent-battleroyal-begin-semantic-json-1"
COMMON_NEWITEM_STRING_PCS = [
	0x0394, 0x039A, 0x03A0, 0x03A6, 0x03AC, 0x03B2, 0x03B8, 0x03BE,
	0x03C4, 0x03CA, 0x03D0, 0x03D6, 0x03DC, 0x03E2, 0x03E8, 0x03EE,
	0x040A,
]
MONEY_ITEM_SCRIPT_PC = 0x075F
RIVAL_MODEL_PCS = [
	0x10D4, 0x10D9, 0x10DE, 0x10E3, 0x10E8, 0x10ED, 0x10F2, 0x10F7,
	0x10FC, 0x1101, 0x1106, 0x110B, 0x1110, 0x1115, 0x111A, 0x111F,
	0x1124, 0x1129, 0x112E, 0x1133, 0x1138, 0x113D, 0x1142, 0x1147,
	0x114C, 0x1151, 0x1156, 0x115B, 0x1160, 0x1165, 0x116A,
]
LAYOUT_STRING_GROUPS = {
	"world_camera_motion": [0x1170],
	"dummy_script": [0x117B],
	"opening_sequence_name": [0x1188],
	"archive_path": [0x118B, 0x11B2, 0x123B],
	"main_layout_name": [0x118C],
	"main_state_begin": [0x1190, 0x1199],
	"main_state_end": [0x11A6, 0x11AB],
	"notice_layout_name": [0x11B3],
	"notice_label_requirement": [0x1228],
	"notice_state_begin": [0x122D, 0x1232],
	"notice_state_loop": [0x1237],
	"rival_layout_name": [0x123C],
	"rival_label_type": [0x1286, 0x1295],
	"rival_label_ap": [0x12DB],
	"rival_label_dp": [0x1318],
	"rival_state_begin1": [0x132B, 0x1335],
	"rival_state_begin2": [0x1330, 0x1343],
	"rival_state_normal": [0x1353, 0x135A],
	"rival_state_end": [0x1369, 0x136E, 0x137A, 0x137F, 0x1384, 0x1389],
}
TOP_LABEL_PCS = {
	"grade": [0x11CD],
	"area": [0x11D2],
	"prize": [0x11D9],
}
AUDIO_INT_PCS = {
	"opening_se": [0x1184],
	"notice_open_se": [0x11A2],
	"rival_begin1_se": [0x1327],
	"rival_begin2_se": [0x133D],
	"rival_normal_se": [0x134F],
}
AP_THRESHOLD_PCS = [0x12AB, 0x12B4, 0x12BD, 0x12C6, 0x12CF]
DP_THRESHOLD_PCS = [0x12E8, 0x12F1, 0x12FA, 0x1303, 0x130C]


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
			pc_to_string_index[pc] = int(instruction.get("decoded", {}).get("index"))
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
	instruction["decoded"] = {
		"type": "int24",
		"value_signed": int(value),
		"value_unsigned": instruction["imm"],
	}


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
			"linked_event_scripts": read_string_list(string_entries, pc_to_string_index, COMMON_NEWITEM_STRING_PCS),
			"money_item_script": read_push_str(string_entries, pc_to_string_index, MONEY_ITEM_SCRIPT_PC)[0],
		},
		"rival_resources": {
			"rival_model_brres": read_string_list(string_entries, pc_to_string_index, RIVAL_MODEL_PCS),
		},
		"layout_assets": {
			key: read_push_str(string_entries, pc_to_string_index, pcs[0])[0]
			for key, pcs in LAYOUT_STRING_GROUPS.items()
		},
		"ui_labels": {
			key: read_push_str(string_entries, pc_to_string_index, pcs[0])[0]
			for key, pcs in TOP_LABEL_PCS.items()
		},
		"audio": {
			key: read_push_int(pc_to_instruction, pcs[0])
			for key, pcs in AUDIO_INT_PCS.items()
		},
		"stat_thresholds": {
			"ap_rank_thresholds_desc": read_int_list(pc_to_instruction, AP_THRESHOLD_PCS),
			"dp_rank_thresholds_desc": read_int_list(pc_to_instruction, DP_THRESHOLD_PCS),
		},
	}


def apply_semantic(document, semantic):
	if semantic.get("format") != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format: {semantic.get('format')!r}")
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	write_push_str_group(string_entries, pc_to_string_index, COMMON_NEWITEM_STRING_PCS, semantic["embedded_scripts"]["linked_event_scripts"])
	write_push_str_group(string_entries, pc_to_string_index, [MONEY_ITEM_SCRIPT_PC], [semantic["embedded_scripts"]["money_item_script"]])
	write_push_str_group(string_entries, pc_to_string_index, RIVAL_MODEL_PCS, semantic["rival_resources"]["rival_model_brres"])
	for key, pcs in LAYOUT_STRING_GROUPS.items():
		write_push_str_group(string_entries, pc_to_string_index, pcs, [semantic["layout_assets"][key]] * len(pcs))
	for key, pcs in TOP_LABEL_PCS.items():
		write_push_str_group(string_entries, pc_to_string_index, pcs, [semantic["ui_labels"][key]] * len(pcs))
	for key, pcs in AUDIO_INT_PCS.items():
		for pc in pcs:
			write_push_int(pc_to_instruction, pc, semantic["audio"][key])
	write_int_list(pc_to_instruction, AP_THRESHOLD_PCS, semantic["stat_thresholds"]["ap_rank_thresholds_desc"], "stat_thresholds.ap_rank_thresholds_desc")
	write_int_list(pc_to_instruction, DP_THRESHOLD_PCS, semantic["stat_thresholds"]["dp_rank_thresholds_desc"], "stat_thresholds.dp_rank_thresholds_desc")
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


def build_parser():
	parser = argparse.ArgumentParser(description="Edit CEvent_battleroyal_begin.pkc via semantic JSON.")
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
	parser = build_parser()
	args = parser.parse_args()
	args.func(args)


if __name__ == "__main__":
	main()
