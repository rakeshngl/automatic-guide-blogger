from pydantic import BaseModel, Field


class GuideMeta(BaseModel):
    title: str
    tagline: str
    navbar_badge: str = "guide"
    hero_paragraph: str = ""


class WarningBox(BaseModel):
    heading: str
    bullets: list[str] = Field(min_length=1)


class DiagramNode(BaseModel):
    id: str
    label: str
    sublabel: str | None = None
    group: str | None = None
    style: str = "default"


class DiagramGroup(BaseModel):
    id: str
    label: str
    style: str = "plain"


class DiagramEdge(BaseModel):
    from_node: str = Field(alias="from")
    to_node: str = Field(alias="to")
    label: str | None = None
    bidirectional: bool = False

    model_config = {"populate_by_name": True}


class DiagramSpec(BaseModel):
    title: str
    groups: list[DiagramGroup] = Field(default_factory=list)
    nodes: list[DiagramNode] = Field(min_length=2)
    edges: list[DiagramEdge] = Field(default_factory=list)


class CodeBlock(BaseModel):
    lang: str = "bash"
    filename: str | None = None
    code: str


class Phase(BaseModel):
    title: str
    prose: list[str] = Field(default_factory=list)
    code_blocks: list[CodeBlock] = Field(default_factory=list)


class ChecklistRow(BaseModel):
    check: str
    command_why: str


class ClosingAlert(BaseModel):
    heading: str
    body: str


class Guide(BaseModel):
    meta: GuideMeta
    intro: list[str] = Field(default_factory=list)
    warning_box: WarningBox | None = None
    diagram: DiagramSpec | None = None
    phases: list[Phase] = Field(min_length=4)
    checklist: list[ChecklistRow] = Field(min_length=3)
    closing_alert: ClosingAlert | None = None
