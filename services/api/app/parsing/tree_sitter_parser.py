from bisect import bisect_right
import re

from tree_sitter import Language, Node, Parser

from app.parsing.base import CodeParsingError, ParsedSource, SourceSyntaxError, SyntaxSymbol
from app.repository.models import RepositoryFile


class TreeSitterCodeParser:
    """Extract named lexical declarations; this is not a compiler or chunker."""

    def __init__(
        self, language: str, grammar: Language, declaration_node_types: frozenset[str],
    ) -> None:
        self._language = language
        self._grammar = grammar
        self._declaration_node_types = declaration_node_types

    @property
    def language(self) -> str:
        return self._language

    def parse(self, file: RepositoryFile) -> ParsedSource:
        if file.language != self.language:
            raise CodeParsingError("Source language does not match the selected parser.")
        try:
            encoded = file.content.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise CodeParsingError("Source cannot be encoded as UTF-8.") from exc

        # Java grammar line comments only terminate on LF. Map lone CR to LF in
        # the parse buffer, one byte for one byte; source and byte spans stay exact.
        parse_buffer = re.sub(rb"\r(?!\n)", b"\n", encoded)
        # A fresh native Parser per call avoids shared mutable parsing state.
        try:
            tree = Parser(self._grammar).parse(parse_buffer)
        except (ValueError, TypeError) as exc:
            raise CodeParsingError("The native parser could not parse this source.") from exc
        root = tree.root_node
        if root.has_error:
            raise SourceSyntaxError("Source contains syntax errors; AST symbols are unavailable.")

        # Match existing Scanner/Chunker splitlines semantics, including lone CR.
        # Convert byte spans against the ORIGINAL source, not native Point rows.
        line_starts: list[int] = []
        byte_offset = 0
        for line in file.content.splitlines(keepends=True):
            line_starts.append(byte_offset)
            byte_offset += len(line.encode("utf-8"))

        symbols: list[SyntaxSymbol] = []
        pending = [(root, None)]
        while pending:
            node, parent_symbol = pending.pop()
            # These bodies have no named owner in this Java spike. Do not invent
            # an outer-class method symbol for an anonymous-class implementation.
            if node.type == "lambda_expression" or (
                node.type == "class_body" and node.parent is not None
                and node.parent.type in {"object_creation_expression", "enum_constant"}
            ):
                continue

            scope = parent_symbol
            # Object literals have named methods but are not declaration chunks.
            # A direct binding supplies their lexical owner, e.g. api.refund.
            if self.language in {"javascript", "typescript"} and node.type == "object":
                binding = self._binding(node)
                if binding is None:
                    continue
                name_node, _ = binding
                name = encoded[name_node.start_byte:name_node.end_byte].decode("utf-8")
                scope = f"{parent_symbol}.{name}" if parent_symbol else name
            if node.type in self._declaration_node_types:
                span = node
                if node.type in {"arrow_function", "function_expression", "generator_function", "class"}:
                    binding = self._binding(node)
                    # Unbound callbacks/anonymous expressions remain source text
                    # inside their containing chunk; never fabricate a symbol.
                    if binding is None:
                        continue
                    name_node, span = binding
                else:
                    name_node = node.child_by_field_name("name")
                if name_node is None or name_node.is_missing or span.end_byte <= span.start_byte:
                    raise CodeParsingError("A declaration has no trustworthy name or source range.")
                if self.language in {"javascript", "typescript"} and name_node.type not in {
                    "identifier", "type_identifier", "property_identifier", "private_property_identifier",
                }:
                    # Computed/string property names have no resolved static owner.
                    continue
                # Include the original decorators/export keyword without adding
                # synthetic text or changing the native declaration node_type.
                if span.parent is not None and span.parent.type in {"decorated_definition", "export_statement"}:
                    span = span.parent
                name = encoded[name_node.start_byte:name_node.end_byte].decode("utf-8")
                symbol = f"{parent_symbol}.{name}" if parent_symbol else name
                symbols.append(SyntaxSymbol(
                    node_type=node.type, name=name, symbol=symbol,
                    parent_symbol=parent_symbol,
                    start_line=bisect_right(line_starts, span.start_byte),
                    end_line=bisect_right(line_starts, span.end_byte - 1),
                    start_byte=span.start_byte, end_byte=span.end_byte,
                    start_point=tuple(span.start_point), end_point=tuple(span.end_point),
                ))
                scope = symbol
            pending.extend((child, scope) for child in reversed(node.named_children))

        return ParsedSource(
            repository=file.repository, path=file.path, language=file.language,
            root_node_type=root.type, symbols=tuple(symbols),
        )

    @staticmethod
    def _binding(node: Node) -> tuple[Node, Node] | None:
        """Only direct, static bindings; no name resolution or type inference."""
        parent = node.parent
        if parent is None or parent.child_by_field_name("value") != node:
            return None
        field = {
            "variable_declarator": "name", "pair": "key",
            "field_definition": "property", "public_field_definition": "name",
        }.get(parent.type)
        if field is None:
            return None
        name = parent.child_by_field_name(field)
        if name is None or name.is_missing or name.type not in {
            "identifier", "property_identifier", "private_property_identifier",
        }:
            return None
        return name, parent
