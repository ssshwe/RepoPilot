from fastapi import APIRouter

from app.llm.factory import get_llm_provider
from app.schemas.analysis import AnalysisRequest, RequirementAnalysis
from app.schemas.chat import ChatMessage


router = APIRouter(prefix="/api", tags=["analysis"])

ANALYSIS_SYSTEM_PROMPT = """你是一名软件需求分析师。请把用户的软件需求转换为指定的 JSON Schema。
summary 使用简洁中文概括需求；difficulty 只能是 easy、medium 或 hard；tasks 列出可执行的实现步骤；risks 列出主要风险，没有风险时返回空数组。
只返回符合 Schema 的数据，不要输出 Markdown、代码围栏或额外解释。"""


@router.post("/analyze", response_model=RequirementAnalysis)
async def analyze(request: AnalysisRequest) -> RequirementAnalysis:
    # Resolve configuration after FastAPI validates the public request shape.
    provider = get_llm_provider()
    return await provider.complete_structured(
        [
            ChatMessage(role="system", content=ANALYSIS_SYSTEM_PROMPT),
            ChatMessage(role="user", content=request.requirement),
        ],
        RequirementAnalysis,
    )
