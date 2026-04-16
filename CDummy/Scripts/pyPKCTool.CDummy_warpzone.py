#!/usr/bin/env python3
import argparse
import copy
import json
import os
import struct
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if SCRIPT_DIR not in sys.path:
	sys.path.insert(0, SCRIPT_DIR)

try:
	import pyPKCToolBase as generic
except ImportError as exc:
	print(f"Failed to import pyPKCToolBase.py: {exc}", file=sys.stderr)
	sys.exit(1)

FORMAT_VERSION = 'cdummy-warpzone-semantic-json-1'
TOOL_DESCRIPTION = 'Edit CDummy_warpzone.pkc via semantic JSON.'
SPECS = [{'name': 'embedded_event_scripts', 'kind': 'string_list', 'pcs': [916, 922, 928, 934, 940, 946, 952, 958, 964, 970, 976, 982, 988, 994, 1000, 1006, 1034]}, {'name': 'money_item_script', 'kind': 'string', 'pc': 1887}, {'name': 'difficulty_rank_labels', 'kind': 'int_list', 'pcs': [2136, 2141, 2146, 2158, 2163, 2168, 2173, 2178]}, {'name': 'waza_desc_fields', 'kind': 'int_list', 'pcs': [2214, 2218, 2222, 2226, 2230]}, {'name': 'team_box_prompt', 'kind': 'mixed_dict', 'entries': [('required_team_size_primary', 'int', 2304), ('required_team_size_secondary', 'int', 2313), ('team_box_full_mid', 'int', 2321)]}, {'name': 'warpzone_assets', 'kind': 'mixed_dict', 'entries': [('grade_s_model', 'string', 2483), ('grade_a_model', 'string', 2488), ('grade_b_model', 'string', 2493), ('grade_c_model', 'string', 2498), ('normal_model', 'string', 2510), ('small_model', 'string', 2515), ('grade_s_fall_motion', 'string', 2526), ('grade_a_fall_motion', 'string', 2529), ('grade_b_fall_motion', 'string', 2532), ('grade_c_fall_motion', 'string', 2535), ('normal_fall_motion', 'string', 2545), ('small_fall_motion', 'string', 2548)]}, {'name': 'balloon_messages', 'kind': 'mixed_dict', 'entries': [('stage_grade_mid', 'int', 2681), ('battle_royal_mid', 'int', 2745), ('extra_round_mid', 'int', 2755), ('fallback_mid', 'int', 2769), ('adjust_y', 'float', 2696)]}]


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


def signed24_to_u24(value):
	integer = int(value)
	if not -(1 << 23) <= integer < (1 << 23):
		raise ValueError(f"Value out of signed 24-bit range: {integer}")
	return integer & 0xFFFFFF


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
	index_to_value = {}
	for pc, value in zip(pcs, values):
		if pc not in pc_to_string_index:
			raise ValueError(f"Expected push_str at PC 0x{pc:04X}, but none was found")
		index = pc_to_string_index[pc]
		if index in index_to_value and index_to_value[index] != value:
			raise ValueError(f"Conflicting values for shared string index {index}: {index_to_value[index]!r} vs {value!r}")
		index_to_value[index] = str(value)
	for index, value in index_to_value.items():
		string_entries[index] = value


def read_string_list(string_entries, pc_to_string_index, pcs):
	return [read_push_str(string_entries, pc_to_string_index, pc)[0] for pc in pcs]


def read_int_list(pc_to_instruction, pcs):
	return [read_push_int(pc_to_instruction, pc) for pc in pcs]


def write_int_list(pc_to_instruction, pcs, values, label):
	if len(values) != len(pcs):
		raise ValueError(f"{label} must contain {len(pcs)} values, got {len(values)}")
	for pc, value in zip(pcs, values):
		write_push_int(pc_to_instruction, pc, value)


def get_nested(container, path):
	current = container
	for part in path.split('.'):
		current = current[part]
	return current


def set_nested(container, path, value):
	parts = path.split('.')
	current = container
	for part in parts[:-1]:
		current = current.setdefault(part, {})
	current[parts[-1]] = value


def export_semantic(document):
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	semantic = {
		"format": FORMAT_VERSION,
		"source_basename": document.get("source_basename"),
	}
	for spec in SPECS:
		name = spec["name"]
		kind = spec["kind"]
		if kind == "string":
			value = read_push_str(string_entries, pc_to_string_index, spec["pc"])[0]
		elif kind == "int":
			value = read_push_int(pc_to_instruction, spec["pc"])
		elif kind == "float":
			value = read_push_float(pc_to_instruction, spec["pc"])
		elif kind == "string_list":
			value = read_string_list(string_entries, pc_to_string_index, spec["pcs"])
		elif kind == "int_list":
			value = read_int_list(pc_to_instruction, spec["pcs"])
		elif kind == "int_matrix":
			value = [read_int_list(pc_to_instruction, group) for group in spec["pcs_groups"]]
		elif kind == "mixed_dict":
			value = {}
			for entry_name, entry_kind, entry_pc in spec["entries"]:
				if entry_kind == "int":
					entry_value = read_push_int(pc_to_instruction, entry_pc)
				elif entry_kind == "float":
					entry_value = read_push_float(pc_to_instruction, entry_pc)
				elif entry_kind == "string":
					entry_value = read_push_str(string_entries, pc_to_string_index, entry_pc)[0]
				else:
					raise ValueError(f"Unsupported mixed_dict entry kind: {entry_kind}")
				value[entry_name] = entry_value
		else:
			raise ValueError(f"Unsupported spec kind: {kind}")
		set_nested(semantic, name, value)
	return semantic


def apply_semantic(document, semantic):
	if semantic.get("format") != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format: {semantic.get('format')!r}")
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	for spec in SPECS:
		name = spec["name"]
		kind = spec["kind"]
		value = get_nested(semantic, name)
		if kind == "string":
			write_push_str_group(string_entries, pc_to_string_index, [spec["pc"]], [value])
		elif kind == "int":
			write_push_int(pc_to_instruction, spec["pc"], value)
		elif kind == "float":
			write_push_float(pc_to_instruction, spec["pc"], value)
		elif kind == "string_list":
			write_push_str_group(string_entries, pc_to_string_index, spec["pcs"], value)
		elif kind == "int_list":
			write_int_list(pc_to_instruction, spec["pcs"], value, name)
		elif kind == "int_matrix":
			if len(value) != len(spec["pcs_groups"]):
				raise ValueError(f"{name} must contain {len(spec['pcs_groups'])} rows, got {len(value)}")
			for row_index, (pcs, row) in enumerate(zip(spec["pcs_groups"], value)):
				write_int_list(pc_to_instruction, pcs, row, f"{name}[{row_index}]")
		elif kind == "mixed_dict":
			for entry_name, entry_kind, entry_pc in spec["entries"]:
				entry_value = value[entry_name]
				if entry_kind == "int":
					write_push_int(pc_to_instruction, entry_pc, entry_value)
				elif entry_kind == "float":
					write_push_float(pc_to_instruction, entry_pc, entry_value)
				elif entry_kind == "string":
					write_push_str_group(string_entries, pc_to_string_index, [entry_pc], [entry_value])
				else:
					raise ValueError(f"Unsupported mixed_dict entry kind: {entry_kind}")
		else:
			raise ValueError(f"Unsupported spec kind: {kind}")
	return document


def command_export(args):
	document = generic.decode_pkc(args.input)
	semantic = export_semantic(document)
	output = args.output or default_json_output(args.input)
	dump_json(output, semantic)
	print(f"Wrote {output}")


def command_import(args):
	semantic = load_json(args.input)
	document = generic.decode_pkc(args.original_pkc)
	document = apply_semantic(document, semantic)
	output = args.output or default_pkc_output(args.original_pkc)
	with open(output, "wb") as outfile:
		outfile.write(generic.encode_pkc_document(document))
	print(f"Wrote {output}")


def command_verify(args):
	document = generic.decode_pkc(args.input)
	semantic = export_semantic(document)
	rebuilt_document = apply_semantic(copy.deepcopy(document), semantic)
	rebuilt = generic.encode_pkc_document(rebuilt_document)
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


def command_summary(args):
	document = generic.decode_pkc(args.input)
	semantic = export_semantic(document)
	print(f"Format: {semantic['format']}")
	for spec in SPECS:
		value = get_nested(semantic, spec['name'])
		if isinstance(value, list):
			print(f"{spec['name']}: {len(value)} entries")
		elif isinstance(value, dict):
			print(f"{spec['name']}: {', '.join(value.keys())}")
		else:
			print(f"{spec['name']}: {value}")


def build_arg_parser():
	parser = argparse.ArgumentParser(description=TOOL_DESCRIPTION)
	subparsers = parser.add_subparsers(dest="command", required=True)

	export_parser = subparsers.add_parser("export", help="Export a PKC to editable JSON.")
	export_parser.add_argument("input", help="Input PKC file")
	export_parser.add_argument("output", nargs="?", help="Output JSON file")
	export_parser.set_defaults(func=command_export)

	import_parser = subparsers.add_parser("import", help="Rebuild a PKC from exported JSON.")
	import_parser.add_argument("input", help="Input JSON file")
	import_parser.add_argument("original_pkc", help="Original PKC file used for rebuilt naming")
	import_parser.add_argument("output", nargs="?", help="Output PKC file")
	import_parser.set_defaults(func=command_import)

	verify_parser = subparsers.add_parser("verify", help="Decode and immediately rebuild a PKC, then compare bytes.")
	verify_parser.add_argument("input", help="Input PKC file")
	verify_parser.set_defaults(func=command_verify)

	summary_parser = subparsers.add_parser("summary", help="Show a short semantic summary.")
	summary_parser.add_argument("input", help="Input PKC file")
	summary_parser.set_defaults(func=command_summary)

	return parser


def main():
	parser = build_arg_parser()
	args = parser.parse_args()
	args.func(args)


if __name__ == "__main__":
	main()
