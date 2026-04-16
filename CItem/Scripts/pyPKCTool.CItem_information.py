
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


TOOL_DESCRIPTION = "Edit CItem_information.pkc via semantic JSON."
FORMAT_VERSION = "citem-information-semantic-json-1"

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

TERMINAL_VISUAL_PCS = {
	"model_brres": 4399,
	"fall_motion": 4487,
	"activate_se": 4431,
}

INFO_SCAN = {
	"message_open_start": 4280,
	"message_open_end": 4385,
	"panel_start": 4512,
	"panel_end": 5290,
}

TARGET_EVENT_NAMES = {
	"DlgInfoSetTitle",
	"DlgInfoSetLine",
	"DlgInfoSetText",
	"MessageOpen",
}


def find_prev_push_int(instrs, start_idx, stop_idx):
	for index in range(start_idx, stop_idx, -1):
		instruction = instrs[index]
		if instruction["opname"] == "push_int":
			return instruction["pc_units"], int(instruction.get("decoded", {}).get("value_signed"))
	return None, None


def scan_dialog_events(document, start_pc, end_pc, include_titles=True):
	instrs = document["code_section"]["instructions"]
	events = []
	current_panel = None
	last_boundary_idx = 0
	for idx, ins in enumerate(instrs):
		pc = ins["pc_units"]
		if pc < start_pc or pc > end_pc:
			continue
		fn = ins.get("decoded", {}).get("function_name")
		if fn not in TARGET_EVENT_NAMES:
			continue
		segment = instrs[last_boundary_idx + 1:idx]
		message_mid_pcs = []
		message_mids = []
		stat_id_pcs = []
		stat_ids = []
		for seg_idx, seg in enumerate(segment):
			seg_fn = seg.get("decoded", {}).get("function_name")
			if seg_fn == "CatGets":
				global_idx = last_boundary_idx + 1 + seg_idx
				pc_value, value = find_prev_push_int(instrs, global_idx - 1, last_boundary_idx)
				if pc_value is not None:
					message_mid_pcs.append(pc_value)
					message_mids.append(value)
			elif seg_fn == "GetStatValue":
				global_idx = last_boundary_idx + 1 + seg_idx
				pc_value, value = find_prev_push_int(instrs, global_idx - 1, last_boundary_idx)
				if pc_value is not None:
					stat_id_pcs.append(pc_value)
					stat_ids.append(value)
		event = {
			"kind": fn,
			"message_mids": message_mids,
			"message_mid_pcs": message_mid_pcs,
			"stat_ids": stat_ids,
			"stat_id_pcs": stat_id_pcs,
		}
		if fn == "DlgInfoSetTitle" and include_titles:
			current_panel = {
				"title_message_mids": message_mids,
				"title_message_mid_pcs": message_mid_pcs,
				"lines": [],
			}
			events.append(current_panel)
		elif fn in ("DlgInfoSetLine", "DlgInfoSetText"):
			line_entry = {
				"kind": "text" if fn == "DlgInfoSetText" else "line",
				"message_mids": message_mids,
				"stat_ids": stat_ids,
			}
			if current_panel is None:
				events.append({"title_message_mids": [], "title_message_mid_pcs": [], "lines": [line_entry]})
				current_panel = events[-1]
			else:
				current_panel["lines"].append(line_entry)
		elif fn == "MessageOpen":
			events.append({
				"kind": "message_open",
				"message_mids": message_mids,
				"message_mid_pcs": message_mid_pcs,
				"stat_ids": stat_ids,
				"stat_id_pcs": stat_id_pcs,
			})
		last_boundary_idx = idx
	return events


def apply_scanned_panel_values(document, panels_json):
	instrs = document["code_section"]["instructions"]
	panels_scan = scan_dialog_events(document, INFO_SCAN["panel_start"], INFO_SCAN["panel_end"])
	panels_only = [entry for entry in panels_scan if "lines" in entry]
	if len(panels_only) != len(panels_json):
		raise ValueError(f"info_panels must contain {len(panels_only)} panels, got {len(panels_json)}")
	pc_to_instruction, _ = build_pc_maps(document)
	for scanned_panel, json_panel in zip(panels_only, panels_json):
		if len(scanned_panel["title_message_mid_pcs"]) != len(json_panel["title_message_mids"]):
			raise ValueError("A panel title message count changed")
		for pc, value in zip(scanned_panel["title_message_mid_pcs"], json_panel["title_message_mids"]):
			write_push_int(pc_to_instruction, pc, value)
		if len(scanned_panel["lines"]) != len(json_panel["lines"]):
			raise ValueError("A panel line count changed")
		for scanned_line, json_line in zip(scanned_panel["lines"], json_panel["lines"]):
			if len(scanned_line["message_mids"]) != len(json_line["message_mids"]):
				raise ValueError("A panel line message count changed")
			if len(scanned_line["stat_ids"]) != len(json_line["stat_ids"]):
				raise ValueError("A panel line stat-id count changed")
			# rescan exact PCs from the source event order
			# find matching scanned event with pcs
			# rebuild by walking again
		pass


def export_semantic(document):
	pc_to_instruction, pc_to_string_index = build_pc_maps(document)
	string_entries = document["string_section"]["entries"]
	message_prompts = []
	for event in scan_dialog_events(document, INFO_SCAN["message_open_start"], INFO_SCAN["message_open_end"], include_titles=False):
		if event.get("kind") == "message_open":
			message_prompts.append({
				"message_mids": event["message_mids"],
				"stat_ids": event["stat_ids"],
			})
	panels = []
	for panel in scan_dialog_events(document, INFO_SCAN["panel_start"], INFO_SCAN["panel_end"]):
		if "lines" not in panel:
			continue
		panels.append({
			"title_message_mids": panel["title_message_mids"],
			"lines": panel["lines"],
		})
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
		"terminal_visuals": {
			"model_brres": read_push_str(string_entries, pc_to_string_index, TERMINAL_VISUAL_PCS["model_brres"])[0],
			"fall_motion": read_push_str(string_entries, pc_to_string_index, TERMINAL_VISUAL_PCS["fall_motion"])[0],
			"activate_se": read_push_int(pc_to_instruction, TERMINAL_VISUAL_PCS["activate_se"]),
		},
		"message_prompts": message_prompts,
		"info_panels": panels,
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
	write_push_str_group(string_entries, pc_to_string_index, [TERMINAL_VISUAL_PCS["model_brres"]], [semantic["terminal_visuals"]["model_brres"]])
	write_push_str_group(string_entries, pc_to_string_index, [TERMINAL_VISUAL_PCS["fall_motion"]], [semantic["terminal_visuals"]["fall_motion"]])
	write_push_int(pc_to_instruction, TERMINAL_VISUAL_PCS["activate_se"], semantic["terminal_visuals"]["activate_se"])

	message_prompt_scan = [entry for entry in scan_dialog_events(document, INFO_SCAN["message_open_start"], INFO_SCAN["message_open_end"], include_titles=False) if entry.get("kind") == "message_open"]
	if len(message_prompt_scan) != len(semantic["message_prompts"]):
		raise ValueError("message_prompts count changed")
	for scanned, json_entry in zip(message_prompt_scan, semantic["message_prompts"]):
		if len(scanned["message_mid_pcs"]) != len(json_entry["message_mids"]):
			raise ValueError("message_prompts message count changed")
		if len(scanned["stat_id_pcs"]) != len(json_entry["stat_ids"]):
			raise ValueError("message_prompts stat-id count changed")
		for pc, value in zip(scanned["message_mid_pcs"], json_entry["message_mids"]):
			write_push_int(pc_to_instruction, pc, value)
		for pc, value in zip(scanned["stat_id_pcs"], json_entry["stat_ids"]):
			write_push_int(pc_to_instruction, pc, value)

	panel_scan = [entry for entry in scan_dialog_events(document, INFO_SCAN["panel_start"], INFO_SCAN["panel_end"]) if "lines" in entry]
	if len(panel_scan) != len(semantic["info_panels"]):
		raise ValueError("info_panels count changed")
	for scanned_panel, json_panel in zip(panel_scan, semantic["info_panels"]):
		if len(scanned_panel["title_message_mid_pcs"]) != len(json_panel["title_message_mids"]):
			raise ValueError("A panel title message count changed")
		for pc, value in zip(scanned_panel["title_message_mid_pcs"], json_panel["title_message_mids"]):
			write_push_int(pc_to_instruction, pc, value)
		if len(scanned_panel["lines"]) != len(json_panel["lines"]):
			raise ValueError("A panel line count changed")
		# rescan exact pcs by iterating paired line metadata and rebuilding from source scan
		for scanned_line_meta, json_line in zip(scanned_panel["lines"], json_panel["lines"]):
			# derive pcs for this line by scanning source again across event order
			pass

	# second pass to update line/message pcs in stable event order
	all_source_events = scan_dialog_events(document, INFO_SCAN["panel_start"], INFO_SCAN["panel_end"])
	flat_json_lines = []
	for panel in semantic["info_panels"]:
		for line in panel["lines"]:
			flat_json_lines.append(line)
	flat_source_lines = []
	for panel in [entry for entry in all_source_events if "lines" in entry]:
		for line in panel["lines"]:
			flat_source_lines.append(line)
	if len(flat_source_lines) != len(flat_json_lines):
		raise ValueError("Flattened info line count changed")
	for scanned_line, json_line in zip(flat_source_lines, flat_json_lines):
		# recover pcs by rescanning matching event in the original scan structure
		pass

	# stable line-by-line pc write using a dedicated event scan
	panel_event_scan = []
	instrs = document["code_section"]["instructions"]
	last_boundary_idx = 0
	current_panel_idx = -1
	current_line_idx = -1
	for idx, ins in enumerate(instrs):
		pc = ins["pc_units"]
		if pc < INFO_SCAN["panel_start"] or pc > INFO_SCAN["panel_end"]:
			continue
		fn = ins.get("decoded", {}).get("function_name")
		if fn not in TARGET_EVENT_NAMES:
			continue
		segment = instrs[last_boundary_idx + 1:idx]
		message_mid_pcs = []
		stat_id_pcs = []
		for seg_idx, seg in enumerate(segment):
			seg_fn = seg.get("decoded", {}).get("function_name")
			if seg_fn == "CatGets":
				global_idx = last_boundary_idx + 1 + seg_idx
				pc_value, value = find_prev_push_int(instrs, global_idx - 1, last_boundary_idx)
				if pc_value is not None:
					message_mid_pcs.append(pc_value)
			elif seg_fn == "GetStatValue":
				global_idx = last_boundary_idx + 1 + seg_idx
				pc_value, value = find_prev_push_int(instrs, global_idx - 1, last_boundary_idx)
				if pc_value is not None:
					stat_id_pcs.append(pc_value)
		if fn == "DlgInfoSetTitle":
			current_panel_idx += 1
			current_line_idx = -1
			for pc, value in zip(message_mid_pcs, semantic["info_panels"][current_panel_idx]["title_message_mids"]):
				write_push_int(pc_to_instruction, pc, value)
		elif fn in ("DlgInfoSetLine", "DlgInfoSetText"):
			current_line_idx += 1
			json_line = semantic["info_panels"][current_panel_idx]["lines"][current_line_idx]
			if len(message_mid_pcs) != len(json_line["message_mids"]):
				raise ValueError("A line message count changed")
			if len(stat_id_pcs) != len(json_line["stat_ids"]):
				raise ValueError("A line stat-id count changed")
			for pc, value in zip(message_mid_pcs, json_line["message_mids"]):
				write_push_int(pc_to_instruction, pc, value)
			for pc, value in zip(stat_id_pcs, json_line["stat_ids"]):
				write_push_int(pc_to_instruction, pc, value)
		last_boundary_idx = idx
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
