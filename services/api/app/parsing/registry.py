from collections.abc import Sequence

from tree_sitter import Language
import tree_sitter_java
import tree_sitter_javascript
import tree_sitter_python
import tree_sitter_typescript

from app.parsing.base import CodeParser, CodeParsingError, ParsedSource, UnsupportedLanguageError
from app.parsing.tree_sitter_parser import TreeSitterCodeParser
from app.repository.language import detect_language
from app.repository.models import RepositoryFile


JAVA_DECLARATION_NODE_TYPES = frozenset({
    "class_declaration", "interface_declaration", "enum_declaration",
    "record_declaration", "annotation_type_declaration", "method_declaration",
    "constructor_declaration", "compact_constructor_declaration",
})
PYTHON_DECLARATION_NODE_TYPES = frozenset({"class_definition", "function_definition"})
JAVASCRIPT_DECLARATION_NODE_TYPES = frozenset({
    "class_declaration", "function_declaration", "generator_function_declaration",
    "method_definition", "arrow_function", "function_expression", "generator_function", "class",
})
TYPESCRIPT_DECLARATION_NODE_TYPES = JAVASCRIPT_DECLARATION_NODE_TYPES | frozenset({
    "interface_declaration", "type_alias_declaration", "abstract_class_declaration",
    "method_signature", "abstract_method_signature", "function_signature",
    "internal_module", "enum_declaration",
})


class ParserRegistry:
    def __init__(self, parsers: Sequence[CodeParser]) -> None:
        self._parsers: dict[str, CodeParser] = {}
        for parser in parsers:
            if parser.language in self._parsers:
                raise ValueError("A parser is already registered for this language.")
            self._parsers[parser.language] = parser

    @property
    def supported_languages(self) -> tuple[str, ...]:
        return tuple(sorted(self._parsers))

    def get_parser(self, language: str) -> CodeParser | None:
        return self._parsers.get(language)

    def parse(self, file: RepositoryFile) -> ParsedSource:
        language = detect_language(file.path)
        if language != file.language:
            raise CodeParsingError("Source language does not match its path extension.")
        parser = self.get_parser(language)
        if parser is None:
            raise UnsupportedLanguageError("No AST parser is registered for this language.")
        return parser.parse(file)


def create_parser_registry() -> ParserRegistry:
    return ParserRegistry([
        TreeSitterCodeParser("java", Language(tree_sitter_java.language()), JAVA_DECLARATION_NODE_TYPES),
        TreeSitterCodeParser("python", Language(tree_sitter_python.language()), PYTHON_DECLARATION_NODE_TYPES),
        TreeSitterCodeParser(
            "javascript", Language(tree_sitter_javascript.language()), JAVASCRIPT_DECLARATION_NODE_TYPES,
        ),
        TreeSitterCodeParser(
            "typescript", Language(tree_sitter_typescript.language_typescript()), TYPESCRIPT_DECLARATION_NODE_TYPES,
        ),
    ])
