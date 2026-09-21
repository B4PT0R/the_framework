import re

from modict import modict


class Base(modict):
    _config = modict.config(enforce_json=True, require_all="never")


class TypedBase(Base):
    unknown_type_cls = modict.attr(None)

    @property
    def types(self):
        return get_all_types(self.__class__)

    @modict.computed
    def type(self):
        return to_snake_case(self.__class__.__name__)

    @classmethod
    def from_dict(cls, payload):
        if isinstance(payload, cls):
            return payload
        if hasattr(payload, "to_dict"):
            payload = payload.to_dict()
        elif hasattr(payload, "model_dump"):
            payload = payload.model_dump()
        item_type = payload.get("type", "unknown")
        item_cls = type_cls(cls, item_type)
        if item_cls is not cls:
            return item_cls.from_dict(payload)
        return item_cls(**payload)


def to_snake_case(camel_case_str):
    snake_str = re.sub(r'(?<!^)(?=[A-Z])', '_', camel_case_str).lower()
    return snake_str


def to_class_name(type_str):
    return "".join(part.capitalize() for part in re.split(r"[._]", type_str))


def get_all_types(cls):
    return list(to_snake_case(t.__name__) for t in reversed(cls.__mro__))


def walk_subclasses(cls):
    for subclass in cls.__subclasses__():
        yield subclass
        yield from walk_subclasses(subclass)


def type_cls(cls, item_type):
    class_name = to_class_name(item_type)
    unknown_type_cls = getattr(cls, "unknown_type_cls", None)
    for subclass in walk_subclasses(cls):
        if subclass is unknown_type_cls:
            continue
        if subclass.__name__ == class_name:
            return subclass
    return cls.__dict__.get("unknown_type_cls") or cls
