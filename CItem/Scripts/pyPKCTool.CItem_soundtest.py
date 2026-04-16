#!/usr/bin/env python3
import argparse
import copy
import json
import os
import sys

try:
	from pyPKCToolBase import decode_pkc, encode_pkc_document
except ImportError as exc:
	print(f"Failed to import pyPKCToolBase.py: {exc}", file=sys.stderr)
	sys.exit(1)


TOOL_DESCRIPTION = "Edit CItem_soundtest.pkc via minimal semantic JSON."
FORMAT_VERSION = "citem-soundtest-semantic-json-1"


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


def export_semantic(document):
	instructions = document["code_section"]["instructions"]
	if len(instructions) != 2:
		raise ValueError("CItem_soundtest.pkc is expected to contain exactly 2 instructions")
	prologue = instructions[0]
	exit_instruction = instructions[1]
	if prologue["opname"] != "function_prologue" or exit_instruction["opname"] != "exit":
		raise ValueError("Unexpected CItem_soundtest.pkc structure")
	return {
		"format": FORMAT_VERSION,
		"source_basename": document.get("source_basename"),
		"script": {
			"function_prologue_payload_u32": int(prologue.get("decoded", {}).get("value_u32", 0)),
			"is_empty_stub": True,
		},
	}


def apply_semantic(document, semantic):
	if semantic.get("format") != FORMAT_VERSION:
		raise ValueError(f"Unsupported JSON format: {semantic.get('format')!r}")
	instructions = document["code_section"]["instructions"]
	if len(instructions) != 2:
		raise ValueError("CItem_soundtest.pkc is expected to contain exactly 2 instructions")
	prologue = instructions[0]
	if prologue["opname"] != "function_prologue":
		raise ValueError("Unexpected first instruction in CItem_soundtest.pkc")
	payload = int(semantic["script"]["function_prologue_payload_u32"])
	if not 0 <= payload <= 0xFFFFFFFF:
		raise ValueError("script.function_prologue_payload_u32 must be in 0..0xFFFFFFFF")
	prologue["payload_hex"] = payload.to_bytes(4, "big").hex().upper()
	prologue["decoded"] = {"type": "function_prologue_payload", "value_u32": payload, "value_hex": f"0x{payload:08X}"}
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
