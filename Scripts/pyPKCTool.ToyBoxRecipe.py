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
	from pyPKCEnumLib import SpeciesEnum
except ImportError as exc:
	print(f"Failed to import support modules: {exc}", file=sys.stderr)
	sys.exit(1)

FORMAT_VERSION = "toyboxrecipe-semantic-json-1"

SPECIES_ID_TO_SYMBOL = {int(member): member.name for member in SpeciesEnum}
SPECIES_SYMBOL_TO_ID = {member.name: int(member) for member in SpeciesEnum}

EVENT_SCRIPT_PCS = [0x0AE2, 0x0AE8, 0x0AEE, 0x0AF4, 0x0AFA, 0x0B00, 0x0B06, 0x0B0C, 0x0B12, 0x0B18, 0x0B1E, 0x0B24, 0x0B2A, 0x0B30, 0x0B36, 0x0B3C, 0x0B58]
MONEY_ITEM_SCRIPT_PC = 0x0EAD
TICKET_FOUND_LABEL_PCS = [0x16AB, 0x38D5]
TICKET_ITEM_SCRIPT_PC = 0x3A29

SELECTOR_MESSAGE_PCS = [0x062F, 0x0633, 0x0637, 0x063B, 0x064A, 0x064E, 0x0652, 0x0656, 0x065F]
DIFFICULTY_LABEL_PCS = [0x0FA6, 0x0FAB, 0x0FB0]
RANK_LABEL_PCS = [0x0FBC, 0x0FC1, 0x0FC6, 0x0FCB, 0x0FD0]
REWARD_MESSAGE_PCS = [0x38E7, 0x38F1, 0x38FD, 0x3903, 0x3948, 0x395B, 0x396C, 0x397D]

CATGET_TABLE_PCS = {
	"species_name": {"base_pc": 0x15EA, "subtract_pc": 0x15ED},
	"move_name": {"base_pc": 0x1601, "subtract_pc": 0x1604},
	"species_description": {"base_pc": 0x1618, "subtract_pc": 0x161B},
	"move_description": {"base_pc": 0x162F, "subtract_pc": 0x1632},
}

WAZA_DESC_FIELD_PCS = {
	"preview_fields": [0x0FF4, 0x0FF8, 0x0FFC, 0x1000, 0x1004],
	"classification_field": 0x1068,
	"move_description_field": 0x106C,
	"move_name_field": 0x1070,
	"length_compare_field": [0x112C, 0x122A, 0x1254, 0x1382, 0x13AC, 0x13D6, 0x1405, 0x1433],
}

PPR_FIELD_PCS = {
	"collection_or_category": 0x00B5,
	"attack": 0x02A8,
	"boss_attack_multiplier": 0x02BE,
	"defense": 0x0318,
	"boss_defense_multiplier": 0x032E,
	"boss_speed_override": 0x0348,
	"speed": 0x0352,
	"boss_scalar_x1_or_x3": 0x037C,
	"hp": 0x0419,
	"boss_hp_multiplier": 0x042C,
	"bonus_attack": 0x0504,
	"bonus_defense": 0x0508,
	"bonus_speed": 0x050C,
	"bonus_hp": 0x0510,
}

LEARNSET_SECTION_PCS = {
	"tm_moves": 0x1214,
	"hm_moves": 0x1242,
	"egg_moves": 0x13C4,
	"special_tutor_moves": 0x13F3,
	"tutor_moves": 0x1421,
}

ADD_STAT_VALUE_PCS = {"stat_id_a": 0x391C, "stat_id_b": 0x3920}
REGISTER_SLOT_PCS = {"money_item": [0x0EA1, 0x0EA5, 0x0EA9], "ticket_item": [0x3A21, 0x3A25]}
TICKET_SPAWN_PCS = {"x": 0x3A2F, "y": 0x3A31, "z": 0x3A33}
FORM_VARIANT_COUNT_ROWS = [
	{"species_pc": 0x398D, "count_pc": 0x3991},
	{"species_pc": 0x3998, "count_pc": 0x399C},
	{"species_pc": 0x39A3, "count_pc": 0x39A7},
	{"species_pc": 0x39AE, "count_pc": 0x39B2},
	{"species_pc": 0x39B9, "count_pc": 0x39BD},
	{"species_pc": 0x39C4, "count_pc": 0x39C8},
	{"species_pc": 0x39CF, "count_pc": 0x39D3},
	{"species_pc": 0x39DA, "count_pc": 0x39DE},
	{"species_pc": 0x39E5, "count_pc": 0x39E9},
	{"species_pc": 0x39F0, "count_pc": 0x39F4},
	{"species_pc": 0x39FB, "count_pc": 0x39FF},
	{"species_pc": 0x3A06, "count_pc": 0x3A0A},
	{"species_pc": 0x3A15, "count_pc": 0x3A19},
]


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


def species_id_to_symbol(value):
	value = int(value)
	return SPECIES_ID_TO_SYMBOL.get(value, f"SPECIES_{value:04d}")


def species_value_to_id(value):
	if isinstance(value, int):
		return value
	if isinstance(value, str):
		text = value.strip()
		if text in SPECIES_SYMBOL_TO_ID:
			return SPECIES_SYMBOL_TO_ID[text]
		if text.startswith("SPECIES_"):
			return int(text.split("_", 1)[1], 10)
	return int(value)


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
	integer = int(value)
	if not -(1 << 23) <= integer < (1 << 23):
		raise ValueError(f"Signed 24-bit range exceeded: {integer}")
	instruction["imm"] = integer & 0xFFFFFF
	instruction["imm_hex"] = f"0x{instruction['imm']:06X}"
	instruction["decoded"] = {"type": "int24", "value_signed": integer, "value_unsigned": instruction["imm"]}


def read_push_float(pc_to_instruction, pc):
	instruction = expect_instruction(pc_to_instruction, pc, "push_s_f")
	return float(instruction.get("decoded", {}).get("value"))


def write_push_float(pc_to_instruction, pc, value):
	instruction = expect_instruction(pc_to_instruction, pc, "push_s_f")
	float_value = float(value)
	instruction["decoded"] = {"type": "float", "value": float_value, "source_text": repr(float_value), "bit_pattern_hex": None}


def read_push_str(string_entries, pc_to_string_index, pc):
	index = pc_to_string_index.get(pc)
	if index is None:
		raise ValueError(f"Expected push_str at PC 0x{pc:04X}, but none was found")
	return string_entries[index]


def write_push_str_group(string_entries, pc_to_string_index, pcs, values):
	if len(pcs) != len(values):
		raise ValueError("String PC/value length mismatch")
	index_to_value = {}
	for pc, value in zip(pcs, values):
		index = pc_to_string_index.get(pc)
		if index is None:
			raise ValueError(f"Expected push_str at PC 0x{pc:04X}, but none was found")
		text = str(value)
		if index in index_to_value and index_to_value[index] != text:
			raise ValueError(f"Conflicting values for shared string index {index}: {index_to_value[index]!r} vs {text!r}")
		index_to_value[index] = text
	for index, value in index_to_value.items():
		string_entries[index] = value


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
			"event_scripts": [read_push_str(string_entries, pc_to_string_index, pc) for pc in EVENT_SCRIPT_PCS],
			"money_item_script": read_push_str(string_entries, pc_to_string_index, MONEY_ITEM_SCRIPT_PC),
			"ticket_found_label": read_push_str(string_entries, pc_to_string_index, TICKET_FOUND_LABEL_PCS[0]),
			"ticket_item_script": read_push_str(string_entries, pc_to_string_index, TICKET_ITEM_SCRIPT_PC),
		},
		"message_mids": {
			"selector_message_mids": read_int_list(pc_to_instruction, SELECTOR_MESSAGE_PCS),
			"difficulty_label_mids": read_int_list(pc_to_instruction, DIFFICULTY_LABEL_PCS),
			"rank_label_mids": read_int_list(pc_to_instruction, RANK_LABEL_PCS),
			"reward_message_mids": read_int_list(pc_to_instruction, REWARD_MESSAGE_PCS),
		},
		"catget_tables": {
			name: {"base_mid": read_push_int(pc_to_instruction, cfg["base_pc"]), "index_subtract": read_push_int(pc_to_instruction, cfg["subtract_pc"])}
			for name, cfg in CATGET_TABLE_PCS.items()
		},
		"waza_desc_fields": {
			"preview_fields": read_int_list(pc_to_instruction, WAZA_DESC_FIELD_PCS["preview_fields"]),
			"classification_field": read_push_int(pc_to_instruction, WAZA_DESC_FIELD_PCS["classification_field"]),
			"move_description_field": read_push_int(pc_to_instruction, WAZA_DESC_FIELD_PCS["move_description_field"]),
			"move_name_field": read_push_int(pc_to_instruction, WAZA_DESC_FIELD_PCS["move_name_field"]),
			"length_compare_field": read_push_int(pc_to_instruction, WAZA_DESC_FIELD_PCS["length_compare_field"][0]),
		},
		"ppr_fields": {name: read_push_int(pc_to_instruction, pc) for name, pc in PPR_FIELD_PCS.items()},
		"learnset_section_ids": {name: read_push_int(pc_to_instruction, pc) for name, pc in LEARNSET_SECTION_PCS.items()},
		"reward_flow": {
			"add_stat_value_ids": {name: read_push_int(pc_to_instruction, pc) for name, pc in ADD_STAT_VALUE_PCS.items()},
			"new_obj_register_slots": {name: read_int_list(pc_to_instruction, pcs) for name, pcs in REGISTER_SLOT_PCS.items()},
			"ticket_spawn_xyz": {axis: read_push_float(pc_to_instruction, pc) for axis, pc in TICKET_SPAWN_PCS.items()},
			"ticket_form_variant_counts": [{"species": species_id_to_symbol(read_push_int(pc_to_instruction, row["species_pc"])), "form_count": read_push_int(pc_to_instruction, row["count_pc"])} for row in FORM_VARIANT_COUNT_ROWS],
		},
	}


def apply_semantic(document, semantic):
	if semantic.get("format") != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format: {semantic.get('format')!r}")
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]

	write_push_str_group(string_entries, pc_to_string_index, EVENT_SCRIPT_PCS, semantic["embedded_scripts"]["event_scripts"])
	write_push_str_group(string_entries, pc_to_string_index, [MONEY_ITEM_SCRIPT_PC], [semantic["embedded_scripts"]["money_item_script"]])
	write_push_str_group(string_entries, pc_to_string_index, TICKET_FOUND_LABEL_PCS, [semantic["embedded_scripts"]["ticket_found_label"]] * len(TICKET_FOUND_LABEL_PCS))
	write_push_str_group(string_entries, pc_to_string_index, [TICKET_ITEM_SCRIPT_PC], [semantic["embedded_scripts"]["ticket_item_script"]])

	write_int_list(pc_to_instruction, SELECTOR_MESSAGE_PCS, semantic["message_mids"]["selector_message_mids"], "message_mids.selector_message_mids")
	write_int_list(pc_to_instruction, DIFFICULTY_LABEL_PCS, semantic["message_mids"]["difficulty_label_mids"], "message_mids.difficulty_label_mids")
	write_int_list(pc_to_instruction, RANK_LABEL_PCS, semantic["message_mids"]["rank_label_mids"], "message_mids.rank_label_mids")
	write_int_list(pc_to_instruction, REWARD_MESSAGE_PCS, semantic["message_mids"]["reward_message_mids"], "message_mids.reward_message_mids")

	for name, cfg in CATGET_TABLE_PCS.items():
		entry = semantic["catget_tables"][name]
		write_push_int(pc_to_instruction, cfg["base_pc"], entry["base_mid"])
		write_push_int(pc_to_instruction, cfg["subtract_pc"], entry["index_subtract"])

	write_int_list(pc_to_instruction, WAZA_DESC_FIELD_PCS["preview_fields"], semantic["waza_desc_fields"]["preview_fields"], "waza_desc_fields.preview_fields")
	write_push_int(pc_to_instruction, WAZA_DESC_FIELD_PCS["classification_field"], semantic["waza_desc_fields"]["classification_field"])
	write_push_int(pc_to_instruction, WAZA_DESC_FIELD_PCS["move_description_field"], semantic["waza_desc_fields"]["move_description_field"])
	write_push_int(pc_to_instruction, WAZA_DESC_FIELD_PCS["move_name_field"], semantic["waza_desc_fields"]["move_name_field"])
	for pc in WAZA_DESC_FIELD_PCS["length_compare_field"]:
		write_push_int(pc_to_instruction, pc, semantic["waza_desc_fields"]["length_compare_field"])

	for name, pc in PPR_FIELD_PCS.items():
		write_push_int(pc_to_instruction, pc, semantic["ppr_fields"][name])
	for name, pc in LEARNSET_SECTION_PCS.items():
		write_push_int(pc_to_instruction, pc, semantic["learnset_section_ids"][name])
	for name, pc in ADD_STAT_VALUE_PCS.items():
		write_push_int(pc_to_instruction, pc, semantic["reward_flow"]["add_stat_value_ids"][name])
	for name, pcs in REGISTER_SLOT_PCS.items():
		write_int_list(pc_to_instruction, pcs, semantic["reward_flow"]["new_obj_register_slots"][name], f"reward_flow.new_obj_register_slots.{name}")
	for axis, pc in TICKET_SPAWN_PCS.items():
		write_push_float(pc_to_instruction, pc, semantic["reward_flow"]["ticket_spawn_xyz"][axis])
	rows = semantic["reward_flow"]["ticket_form_variant_counts"]
	if len(rows) != len(FORM_VARIANT_COUNT_ROWS):
		raise ValueError(f"reward_flow.ticket_form_variant_counts must contain {len(FORM_VARIANT_COUNT_ROWS)} rows")
	for cfg, row in zip(FORM_VARIANT_COUNT_ROWS, rows):
		write_push_int(pc_to_instruction, cfg["species_pc"], species_value_to_id(row["species"]))
		write_push_int(pc_to_instruction, cfg["count_pc"], row["form_count"])
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


def command_summary(args):
	document = decode_pkc(args.input)
	semantic = export_semantic(document)
	print(f"Event scripts: {len(semantic['embedded_scripts']['event_scripts'])}")
	print(f"Money item script: {semantic['embedded_scripts']['money_item_script']}")
	print(f"Ticket item script: {semantic['embedded_scripts']['ticket_item_script']}")
	print(f"Reward form-variant rows: {len(semantic['reward_flow']['ticket_form_variant_counts'])}")


def build_arg_parser():
	parser = argparse.ArgumentParser(description="Export and rebuild the semantic editable parts of ToyBoxRecipe.pkc.")
	subparsers = parser.add_subparsers(dest="command", required=True)

	summary_parser = subparsers.add_parser("summary", help="Show a short semantic summary.")
	summary_parser.add_argument("input", help="Input PKC file")
	summary_parser.set_defaults(func=command_summary)

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
	parser = build_arg_parser()
	args = parser.parse_args()
	args.func(args)


if __name__ == "__main__":
	main()
