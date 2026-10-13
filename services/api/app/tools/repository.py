from pydantic import BaseModel, ConfigDict

from app.llm.tool_types import FunctionToolDefinition


class GetRepositoryInfoArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RepositoryInfo(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    frontend: str
    backend: str
    database: str


def _get_repository_info_schema() -> dict[str, object]:
    schema = GetRepositoryInfoArguments.model_json_schema()
    schema.pop("title", None)
    return schema


GET_REPOSITORY_INFO_DEFINITION = FunctionToolDefinition(
    name="get_repository_info",
    description=(
        "获取当前项目名称和技术栈。此工具没有任何参数，调用时必须传入空对象 "
        "{}。不得猜测结果。"
    ),
    parameters=_get_repository_info_schema(),
)


def get_repository_info() -> RepositoryInfo:
    """Return the fixed Stage 1 repository fixture; no filesystem is accessed."""
    return RepositoryInfo(
        name="smart-community",
        frontend="Vue 3",
        backend="Spring Boot",
        database="MySQL",
    )
