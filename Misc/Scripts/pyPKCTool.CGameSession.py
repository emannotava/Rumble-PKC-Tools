
#!/usr/bin/env python3
import argparse
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

PUSH_INT = generic.PKCOpCode.push_int.value
PUSH_FLOAT = generic.PKCOpCode.push_s_f.value
PUSH_STR = generic.PKCOpCode.push_str.value
CALL_IMM = generic.PKCOpCode.call_imm.value
POP = generic.PKCOpCode.pop.value


def dump_json(path, document):
	with open(path, "w", encoding="utf-8", newline="\n") as outfile:
		outfile.write(json.dumps(document, ensure_ascii=False, indent="	"))
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
			raise ValueError(
				f"Conflicting values for shared string index {index}: {index_to_value[index]!r} vs {value!r}"
			)
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


def write_float_list(pc_to_instruction, pcs, values, label):
	if len(values) != len(pcs):
		raise ValueError(f"{label} must contain {len(pcs)} values, got {len(values)}")
	for pc, value in zip(pcs, values):
		write_push_float(pc_to_instruction, pc, value)


def load_document(pkc_path):
	return generic.decode_pkc(pkc_path)


def save_document(path, document):
	raw = generic.encode_pkc_document(document)
	with open(path, "wb") as outfile:
		outfile.write(raw)


def command_verify_impl(args, export_semantic):
	document = load_document(args.pkc_path)
	semantic = export_semantic(document)
	rebuilt_document = load_document(args.pkc_path)
	apply_semantic(rebuilt_document, semantic)
	with open(args.pkc_path, "rb") as infile:
		original_raw = infile.read()
	rebuilt_raw = generic.encode_pkc_document(rebuilt_document)
	matches = rebuilt_raw == original_raw
	print(f"Match: {'YES' if matches else 'NO'}")
	if not matches:
		for index, (left, right) in enumerate(zip(rebuilt_raw, original_raw)):
			if left != right:
				print(f"First mismatch at 0x{index:X}: rebuilt=0x{left:02X}, original=0x{right:02X}")
				break
		if len(rebuilt_raw) != len(original_raw):
			print(f"Length mismatch: rebuilt={len(rebuilt_raw)}, original={len(original_raw)}")
		raise SystemExit(1)


def command_export_impl(args, export_semantic):
	document = load_document(args.pkc_path)
	semantic = export_semantic(document)
	output_path = args.output or default_json_output(args.pkc_path)
	dump_json(output_path, semantic)
	print(f"Exported {output_path}")


def command_import_impl(args, apply_semantic):
	document = load_document(args.original_pkc)
	semantic = load_json(args.json_path)
	apply_semantic(document, semantic)
	output_path = args.output_pkc or default_pkc_output(args.original_pkc)
	save_document(output_path, document)
	print(f"Wrote {output_path}")

FORMAT_VERSION = "cgamesession-semantic-json-1"
REGISTER_SLOT_PAIRS = [
	{"name": "register_00", "setter_pc": 0x00BE, "getter_pc": 0x00C8, "kind": "value"},
	{"name": "register_01", "setter_pc": 0x00D1, "getter_pc": 0x00DB, "kind": "value"},
	{"name": "register_02", "setter_pc": 0x00EF, "getter_pc": 0x00F9, "kind": "value"},
	{"name": "register_03", "setter_pc": 0x0102, "getter_pc": 0x010C, "kind": "value"},
	{"name": "register_04", "setter_pc": 0x0120, "getter_pc": 0x012A, "kind": "value"},
	{"name": "register_05", "setter_pc": 0x0133, "getter_pc": 0x013D, "kind": "value"},
	{"name": "register_07", "setter_pc": 0x0151, "getter_pc": 0x015B, "kind": "value"},
	{"name": "register_08", "setter_pc": 0x0164, "getter_pc": 0x016E, "kind": "value"},
	{"name": "register_06", "setter_pc": 0x0177, "getter_pc": 0x0181, "kind": "value"},
	{"name": "register_09", "setter_pc": 0x018A, "getter_pc": 0x0194, "kind": "bool_nonzero"},
	{"name": "register_10", "setter_pc": 0x019F, "getter_pc": 0x01A9, "kind": "bool_nonzero"},
	{"name": "register_11", "setter_pc": 0x01B4, "getter_pc": 0x01BE, "kind": "bool_nonzero"},
	{"name": "register_12", "setter_pc": 0x01C9, "getter_pc": 0x01D3, "kind": "bool_nonzero"},
	{"name": "register_13", "setter_pc": 0x01DE, "getter_pc": 0x01E8, "kind": "bool_nonzero"},
]
PIIPROP_FIELD_INDEX_PCS = {
	"attack_base": 0x02A8,
	"attack_boss_multiplier": 0x02BE,
	"defense_base": 0x0318,
	"defense_boss_multiplier": 0x032E,
	"boss_speed_override": 0x0348,
	"speed_base": 0x0352,
	"boss_scalar": 0x037C,
	"hp_base": 0x0419,
	"hp_boss_multiplier": 0x042C,
	"setter_attack_base": 0x0504,
	"setter_defense_base": 0x0508,
	"setter_speed_base": 0x050C,
	"setter_hp_base": 0x0510,
}
CONSTANT_GROUPS = {
	"curve_primary": [
		{"name": "pow_exponent", "pc": 0x01F3, "type": "float"},
		{"name": "base_add", "pc": 0x01FC, "type": "float"},
		{"name": "growth_pow", "pc": 0x020D, "type": "float"},
		{"name": "odd_multiplier", "pc": 0x0214, "type": "int"},
		{"name": "odd_add", "pc": 0x0216, "type": "int"},
	],
	"curve_secondary": [
		{"name": "scale_multiplier", "pc": 0x0240, "type": "float"},
		{"name": "base_add", "pc": 0x0243, "type": "float"},
	],
	"level_hp_curve": [
		{"name": "pow_exponent", "pc": 0x04D2, "type": "float"},
		{"name": "base_add", "pc": 0x04D5, "type": "float"},
		{"name": "stage_scale", "pc": 0x04DB, "type": "float"},
		{"name": "stage_add", "pc": 0x04E1, "type": "float"},
		{"name": "round_pow", "pc": 0x04E7, "type": "float"},
	],
	"attack_scaling": [
		{"name": "flag_id", "pc": 0x02C2, "type": "int"},
		{"name": "flag_multiplier", "pc": 0x02C7, "type": "float"},
		{"name": "difficulty_ex_id", "pc": 0x02D1, "type": "int"},
		{"name": "difficulty_ex_multiplier", "pc": 0x02D5, "type": "float"},
		{"name": "special_species", "pc": 0x02DE, "type": "int"},
		{"name": "special_species_add", "pc": 0x02E9, "type": "float"},
		{"name": "nonboss_multiplier", "pc": 0x02EF, "type": "float"},
		{"name": "boss_multiplier", "pc": 0x02F5, "type": "float"},
	],
	"speed_scaling": [
		{"name": "special_species", "pc": 0x0361, "type": "int"},
		{"name": "special_species_multiplier", "pc": 0x0365, "type": "float"},
		{"name": "boss_multiplier", "pc": 0x0383, "type": "float"},
		{"name": "nonboss_multiplier", "pc": 0x0389, "type": "float"},
		{"name": "curve_pow", "pc": 0x0395, "type": "float"},
		{"name": "clamp_min", "pc": 0x03A0, "type": "int"},
		{"name": "clamp_offset", "pc": 0x03A5, "type": "int"},
		{"name": "curve_add", "pc": 0x03A8, "type": "float"},
		{"name": "curve_scale_int", "pc": 0x03AF, "type": "int"},
		{"name": "curve_scale_float", "pc": 0x03B2, "type": "float"},
		{"name": "battle_royal_scale", "pc": 0x03FF, "type": "float"},
	],
	"hp_scaling": [
		{"name": "difficulty_ex_id", "pc": 0x0436, "type": "int"},
		{"name": "difficulty_ex_multiplier", "pc": 0x043A, "type": "float"},
		{"name": "difficulty_grade_00", "pc": 0x0452, "type": "float"},
		{"name": "difficulty_grade_01", "pc": 0x0458, "type": "float"},
		{"name": "difficulty_grade_02", "pc": 0x045E, "type": "float"},
		{"name": "difficulty_grade_03", "pc": 0x0464, "type": "float"},
		{"name": "grade_min", "pc": 0x046A, "type": "int"},
		{"name": "grade_00", "pc": 0x0471, "type": "float"},
		{"name": "grade_01", "pc": 0x0477, "type": "float"},
		{"name": "grade_02", "pc": 0x047D, "type": "float"},
		{"name": "grade_03", "pc": 0x0483, "type": "float"},
		{"name": "grade_switch", "pc": 0x0489, "type": "int"},
		{"name": "grade_alt", "pc": 0x048E, "type": "float"},
		{"name": "round_nonboss_a", "pc": 0x0499, "type": "float"},
		{"name": "round_nonboss_b", "pc": 0x049F, "type": "float"},
		{"name": "round_boss", "pc": 0x04A5, "type": "float"},
	],
}


def export_constant_groups(pc_to_instruction):
	groups = {}
	for group_name, items in CONSTANT_GROUPS.items():
		rows = []
		for item in items:
			value = read_push_float(pc_to_instruction, item["pc"]) if item["type"] == "float" else read_push_int(pc_to_instruction, item["pc"])
			rows.append({
				"name": item["name"],
				"pc": f"0x{item['pc']:04X}",
				"type": item["type"],
				"value": value,
			})
		groups[group_name] = rows
	return groups


def apply_constant_groups(pc_to_instruction, groups):
	for group_name, items in CONSTANT_GROUPS.items():
		input_rows = groups[group_name]
		if len(input_rows) != len(items):
			raise ValueError(f"{group_name} must contain {len(items)} rows")
		for template, row in zip(items, input_rows):
			if template["type"] == "float":
				write_push_float(pc_to_instruction, template["pc"], row["value"])
			else:
				write_push_int(pc_to_instruction, template["pc"], row["value"])


def export_semantic(document):
	pc_to_instruction, _pc_to_string_index = build_pc_maps(document)
	return {
		"format": FORMAT_VERSION,
		"source_basename": document.get("source_basename"),
		"ppd_register_slots": [
			{
				"name": entry["name"],
				"slot": read_push_int(pc_to_instruction, entry["setter_pc"]),
				"setter_pc": f"0x{entry['setter_pc']:04X}",
				"getter_pc": f"0x{entry['getter_pc']:04X}",
				"kind": entry["kind"],
			}
			for entry in REGISTER_SLOT_PAIRS
		],
		"piiprop_field_indexes": {key: read_push_int(pc_to_instruction, pc) for key, pc in PIIPROP_FIELD_INDEX_PCS.items()},
		"constant_groups": export_constant_groups(pc_to_instruction),
	}


def apply_semantic(document, semantic):
	if semantic.get("format") != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format: {semantic.get('format')!r}")
	pc_to_instruction, _pc_to_string_index = build_pc_maps(document)
	register_rows = semantic["ppd_register_slots"]
	if len(register_rows) != len(REGISTER_SLOT_PAIRS):
		raise ValueError(f"ppd_register_slots must contain {len(REGISTER_SLOT_PAIRS)} rows")
	for template, row in zip(REGISTER_SLOT_PAIRS, register_rows):
		write_push_int(pc_to_instruction, template["setter_pc"], row["slot"])
		write_push_int(pc_to_instruction, template["getter_pc"], row["slot"])
	for key, pc in PIIPROP_FIELD_INDEX_PCS.items():
		write_push_int(pc_to_instruction, pc, semantic["piiprop_field_indexes"][key])
	apply_constant_groups(pc_to_instruction, semantic["constant_groups"])


def command_export(args):
	command_export_impl(args, export_semantic)


def command_import(args):
	command_import_impl(args, apply_semantic)


def command_verify(args):
	command_verify_impl(args, export_semantic)


def build_arg_parser():
	parser = argparse.ArgumentParser(description="Edit CGameSession.pkc via structured semantic JSON.")
	subparsers = parser.add_subparsers(dest="command", required=True)
	p = subparsers.add_parser("export")
	p.add_argument("pkc_path")
	p.add_argument("output", nargs="?")
	p.set_defaults(func=command_export)
	p = subparsers.add_parser("import")
	p.add_argument("json_path")
	p.add_argument("original_pkc")
	p.add_argument("output_pkc", nargs="?")
	p.set_defaults(func=command_import)
	p = subparsers.add_parser("verify")
	p.add_argument("pkc_path")
	p.set_defaults(func=command_verify)
	return parser


def main():
	parser = build_arg_parser()
	args = parser.parse_args()
	args.func(args)


if __name__ == "__main__":
	main()
