
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
	float_value = float(value)
	instruction["payload_hex"] = struct.pack(">f", float_value).hex().upper()
	instruction["decoded"] = {
		"type": "float",
		"value": float_value,
	}


def read_push_str(string_entries, pc_to_string_index, pc):
	if pc not in pc_to_string_index:
		raise ValueError(f"Expected push_str at PC 0x{pc:04X}, but none was found")
	index = pc_to_string_index[pc]
	return string_entries[index], index


def write_push_str_group(string_entries, pc_to_string_index, pcs, values):
	if len(pcs) != len(values):
		raise ValueError(f"Expected {len(pcs)} values, got {len(values)}")
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


TOOL_DESCRIPTION = "Edit CItem_grade_neji.pkc via semantic JSON."
FORMAT_VERSION = "citem-grade-neji-semantic-json-1"

COMMON_NEWITEM_STRING_PCS = [
	916, 922, 928, 934, 940, 946, 952, 958,
	964, 970, 976, 982, 988, 994, 1000, 1006,
	1034, 1887,
]

COMMON_MESSAGE_PC_MAP = {
	"dynamic_category_base_high": 1386,
	"dynamic_category_base_low": 1397,
	"difficulty_rank_labels": [2136, 2141, 2146, 2158, 2163, 2168, 2173, 2178],
}

WAZA_DESC_FIELD_PCS = [2214, 2218, 2222, 2226, 2230]

GRADE_MODEL_PCS = {
	"default_model_brres": 2308,
	"grade_models": [2311, 2314, 2317, 2320],
	"old_motions": [2323, 2326, 2329, 2332],
	"new_motions": [2335, 2338, 2341, 2344],
}

GRADE_VALUE_PCS = {
	"grade_switch_default_index": 2310,
	"old_motion_default_index": 2322,
}


def export_semantic(document):
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	return {
		"format": FORMAT_VERSION,
		"source_basename": document.get("source_basename"),
		"embedded_scripts": {
			"newitem_sequence": read_string_list(string_entries, pc_to_string_index, COMMON_NEWITEM_STRING_PCS[:-1]),
			"self_script_name": read_push_str(string_entries, pc_to_string_index, COMMON_NEWITEM_STRING_PCS[-1])[0],
		},
		"common_messages": {
			"dynamic_category_base_high": read_push_int(pc_to_instruction, COMMON_MESSAGE_PC_MAP["dynamic_category_base_high"]),
			"dynamic_category_base_low": read_push_int(pc_to_instruction, COMMON_MESSAGE_PC_MAP["dynamic_category_base_low"]),
			"difficulty_rank_labels": read_int_list(pc_to_instruction, COMMON_MESSAGE_PC_MAP["difficulty_rank_labels"]),
		},
		"waza_desc_fields": read_int_list(pc_to_instruction, WAZA_DESC_FIELD_PCS),
		"grade_visuals": {
			"default_model_brres": read_push_str(string_entries, pc_to_string_index, GRADE_MODEL_PCS["default_model_brres"])[0],
			"grade_models": {
				"C": read_push_str(string_entries, pc_to_string_index, GRADE_MODEL_PCS["grade_models"][0])[0],
				"B": read_push_str(string_entries, pc_to_string_index, GRADE_MODEL_PCS["grade_models"][1])[0],
				"A": read_push_str(string_entries, pc_to_string_index, GRADE_MODEL_PCS["grade_models"][2])[0],
				"S": read_push_str(string_entries, pc_to_string_index, GRADE_MODEL_PCS["grade_models"][3])[0],
			},
			"old_motions": {
				"default": read_push_str(string_entries, pc_to_string_index, GRADE_MODEL_PCS["old_motions"][0])[0],
				"C": read_push_str(string_entries, pc_to_string_index, GRADE_MODEL_PCS["old_motions"][1])[0],
				"B": read_push_str(string_entries, pc_to_string_index, GRADE_MODEL_PCS["old_motions"][2])[0],
				"A": read_push_str(string_entries, pc_to_string_index, GRADE_MODEL_PCS["old_motions"][3])[0],
			},
			"new_motions": {
				"C": read_push_str(string_entries, pc_to_string_index, GRADE_MODEL_PCS["new_motions"][0])[0],
				"B": read_push_str(string_entries, pc_to_string_index, GRADE_MODEL_PCS["new_motions"][1])[0],
				"A": read_push_str(string_entries, pc_to_string_index, GRADE_MODEL_PCS["new_motions"][2])[0],
				"S": read_push_str(string_entries, pc_to_string_index, GRADE_MODEL_PCS["new_motions"][3])[0],
			},
		},
	}


def apply_semantic(document, semantic):
	if semantic.get("format") != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format: {semantic.get('format')!r}")
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	write_push_str_group(string_entries, pc_to_string_index, COMMON_NEWITEM_STRING_PCS[:-1], semantic["embedded_scripts"]["newitem_sequence"])
	write_push_str_group(string_entries, pc_to_string_index, [COMMON_NEWITEM_STRING_PCS[-1]], [semantic["embedded_scripts"]["self_script_name"]])
	write_push_int(pc_to_instruction, COMMON_MESSAGE_PC_MAP["dynamic_category_base_high"], semantic["common_messages"]["dynamic_category_base_high"])
	write_push_int(pc_to_instruction, COMMON_MESSAGE_PC_MAP["dynamic_category_base_low"], semantic["common_messages"]["dynamic_category_base_low"])
	write_int_list(pc_to_instruction, COMMON_MESSAGE_PC_MAP["difficulty_rank_labels"], semantic["common_messages"]["difficulty_rank_labels"], "common_messages.difficulty_rank_labels")
	write_int_list(pc_to_instruction, WAZA_DESC_FIELD_PCS, semantic["waza_desc_fields"], "waza_desc_fields")
	gv = semantic["grade_visuals"]
	write_push_str_group(string_entries, pc_to_string_index, [GRADE_MODEL_PCS["default_model_brres"]], [gv["default_model_brres"]])
	write_push_str_group(string_entries, pc_to_string_index, GRADE_MODEL_PCS["grade_models"], [gv["grade_models"]["C"], gv["grade_models"]["B"], gv["grade_models"]["A"], gv["grade_models"]["S"]])
	write_push_str_group(string_entries, pc_to_string_index, GRADE_MODEL_PCS["old_motions"], [gv["old_motions"]["default"], gv["old_motions"]["C"], gv["old_motions"]["B"], gv["old_motions"]["A"]])
	write_push_str_group(string_entries, pc_to_string_index, GRADE_MODEL_PCS["new_motions"], [gv["new_motions"]["C"], gv["new_motions"]["B"], gv["new_motions"]["A"], gv["new_motions"]["S"]])
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
	parser = argparse.ArgumentParser(description=TOOL_DESCRIPTION)
	subparsers = parser.add_subparsers(dest="command", required=True)
	export_parser = subparsers.add_parser("export", help="Export to editable JSON.")
	export_parser.add_argument("input", help="Input PKC file")
	export_parser.add_argument("output", nargs="?", help="Output JSON file")
	export_parser.set_defaults(func=command_export)
	import_parser = subparsers.add_parser("import", help="Import edited JSON and rebuild PKC.")
	import_parser.add_argument("input", help="Input JSON file")
	import_parser.add_argument("original_pkc", help="Original PKC file")
	import_parser.add_argument("output", nargs="?", help="Output PKC file")
	import_parser.set_defaults(func=command_import)
	verify_parser = subparsers.add_parser("verify", help="Semantic no-edit roundtrip check.")
	verify_parser.add_argument("input", help="Input PKC file")
	verify_parser.set_defaults(func=command_verify)
	return parser


def main():
	parser = build_parser()
	args = parser.parse_args()
	args.func(args)


if __name__ == "__main__":
	main()
