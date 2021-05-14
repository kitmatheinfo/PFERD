# I'm sorry that this code has become a bit dense and unreadable. While
# reading, it is important to remember what True and False mean. I'd love to
# have some proper sum-types for the inputs and outputs, they'd make this code
# a lot easier to understand.

import ast
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import PurePath
from typing import Dict, Optional, Union


class Rule(ABC):
    @abstractmethod
    def transform(self, path: PurePath) -> Union[PurePath, bool]:
        """
        Try to apply this rule to the path. Returns another path if the rule
        was successfully applied, True if the rule matched but resulted in an
        exclamation mark, and False if the rule didn't match at all.
        """

        pass


# These rules all use a Union[T, bool] for their right side. They are passed a
# T if the arrow's right side was a normal string, True if it was an
# exclamation mark and False if it was missing entirely.

class NormalRule(Rule):
    def __init__(self, left: PurePath, right: Union[PurePath, bool]):

        self._left = left
        self._right = right

    def _match_prefix(self, path: PurePath) -> Optional[PurePath]:
        left_parts = list(reversed(self._left.parts))
        path_parts = list(reversed(path.parts))

        if len(left_parts) > len(path_parts):
            return None

        while left_parts and path_parts:
            left_part = left_parts.pop()
            path_part = path_parts.pop()

            if left_part != path_part:
                return None

        if left_parts:
            return None

        return PurePath(*path_parts)

    def transform(self, path: PurePath) -> Union[PurePath, bool]:
        if rest := self._match_prefix(path):
            if isinstance(self._right, bool):
                return self._right or path
            else:
                return self._right / rest

        return False


class ExactRule(Rule):
    def __init__(self, left: PurePath, right: Union[PurePath, bool]):
        self._left = left
        self._right = right

    def transform(self, path: PurePath) -> Union[PurePath, bool]:
        if path == self._left:
            if isinstance(self._right, bool):
                return self._right or path
            else:
                return self._right

        return False


class ReRule(Rule):
    def __init__(self, left: str, right: Union[str, bool]):
        self._left = left
        self._right = right

    def transform(self, path: PurePath) -> Union[PurePath, bool]:
        if match := re.fullmatch(self._left, str(path)):
            if isinstance(self._right, bool):
                return self._right or path

            vars: Dict[str, Union[str, int, float]] = {}

            groups = [match[0]] + list(match.groups())
            for i, group in enumerate(groups):
                vars[f"g{i}"] = group

                try:
                    vars[f"i{i}"] = int(group)
                except ValueError:
                    pass

                try:
                    vars[f"f{i}"] = float(group)
                except ValueError:
                    pass

            result = eval(f"f{self._right!r}", vars)
            return PurePath(result)

        return False


@dataclass
class RuleParseException(Exception):
    line: "Line"
    reason: str

    def pretty_print(self) -> None:
        print(f"Error parsing rule on line {self.line.line_nr}:")
        print(self.line.line)
        spaces = " " * self.line.index
        print(f"{spaces}^--- {self.reason}")


class Line:
    def __init__(self, line: str, line_nr: int):
        self._line = line
        self._line_nr = line_nr
        self._index = 0

    def get(self) -> Optional[str]:
        if self._index < len(self._line):
            return self._line[self._index]

        return None

    @property
    def line(self) -> str:
        return self._line

    @property
    def line_nr(self) -> str:
        return self._line

    @property
    def index(self) -> int:
        return self._index

    @index.setter
    def index(self, index: int) -> None:
        self._index = index

    def advance(self) -> None:
        self._index += 1

    def expect(self, string: str) -> None:
        for char in string:
            if self.get() == char:
                self.advance()
            else:
                raise RuleParseException(self, f"Expected {char!r}")


QUOTATION_MARKS = {'"', "'"}


def parse_string_literal(line: Line) -> str:
    escaped = False

    # Points to first character of string literal
    start_index = line.index

    quotation_mark = line.get()
    if quotation_mark not in QUOTATION_MARKS:
        # This should never happen as long as this function is only called from
        # parse_string.
        raise RuleParseException(line, "Invalid quotation mark")
    line.advance()

    while c := line.get():
        if escaped:
            escaped = False
            line.advance()
        elif c == quotation_mark:
            line.advance()
            stop_index = line.index
            literal = line.line[start_index:stop_index]
            return ast.literal_eval(literal)
        elif c == "\\":
            escaped = True
            line.advance()
        else:
            line.advance()

    raise RuleParseException(line, "Expected end of string literal")


def parse_until_space_or_eol(line: Line) -> str:
    result = []
    while c := line.get():
        if c == " ":
            break
        result.append(c)
        line.advance()

    return "".join(result)


def parse_string(line: Line) -> Union[str, bool]:
    if line.get() in QUOTATION_MARKS:
        return parse_string_literal(line)
    else:
        string = parse_until_space_or_eol(line)
        if string == "!":
            return True
        return string


def parse_arrow(line: Line) -> str:
    line.expect("-")

    name = []
    while True:
        if c := line.get():
            if c == "-":
                break
            else:
                name.append(c)
            line.advance()
        else:
            raise RuleParseException(line, "Expected rest of arrow")

    line.expect("->")
    return "".join(name)


def parse_rule(line: Line) -> Rule:
    # Parse left side
    leftindex = line.index
    left = parse_string(line)
    if isinstance(left, bool):
        line.index = leftindex
        raise RuleParseException(line, "Left side can't be '!'")

    # Parse arrow
    line.expect(" ")
    arrowindex = line.index
    arrowname = parse_arrow(line)

    # Parse right side
    if line.get():
        line.expect(" ")
        right = parse_string(line)
    else:
        right = False
    rightpath: Union[PurePath, bool]
    if isinstance(right, bool):
        rightpath = right
    else:
        rightpath = PurePath(right)

    # Dispatch
    if arrowname == "":
        return NormalRule(PurePath(left), rightpath)
    elif arrowname == "exact":
        return ExactRule(PurePath(left), rightpath)
    elif arrowname == "re":
        return ReRule(left, right)
    else:
        line.index = arrowindex + 1  # For nicer error message
        raise RuleParseException(line, "Invalid arrow name")


class Transformer:
    def __init__(self, rules: str):
        """
        May throw a RuleParseException.
        """

        self._rules = []
        for i, line in enumerate(rules.split("\n")):
            line = line.strip()
            if line:
                self._rules.append(parse_rule(Line(line, i)))

    def transform(self, path: PurePath) -> Optional[PurePath]:
        for rule in self._rules:
            result = rule.transform(path)
            if isinstance(result, PurePath):
                return result
            elif result:  # Exclamation mark
                return None
            else:
                continue

        return None