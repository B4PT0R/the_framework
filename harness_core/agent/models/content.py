from .base import TypedBase


class ContentItem(TypedBase):
    pass


class UnknownContentItem(ContentItem):
    type: str


class InputText(ContentItem):
    text: str


class InputImage(ContentItem):
    image_url: str


class OutputText(ContentItem):
    text: str


class Refusal(ContentItem):
    refusal: str


ContentItem.unknown_type_cls = UnknownContentItem
