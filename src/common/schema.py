"""공통 데이터 스키마 (Pydantic)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class Article(BaseModel):
    """기사 1건. Stage 0~4 전체에서 공통 사용."""

    id: str
    source: str  # 도메인 (예: "yakup.com")
    url: str
    title: str
    content: str = ""
    published_at: Optional[datetime] = None
    collected_at: datetime = Field(default_factory=datetime.now)

    # Stage 1 결과
    stage1_passed: Optional[bool] = None
    stage1_matched_patterns: list[str] = Field(default_factory=list)

    # Stage 2 결과
    stage2_relevant: Optional[str] = None  # "Y" | "N"
    stage2_industry: Optional[str] = None  # code (예: "GMP_BIO")
    stage2_reason: Optional[str] = None

    # Stage 3 결과
    stage3_extracted: Optional[dict] = None


class ExtractedProject(BaseModel):
    """Stage 3 LLM 추출 결과 - 영업팀 요구사항 10번 매핑."""

    project_name: Optional[str] = None        # 프로젝트명
    client_name: Optional[str] = None         # 발주처
    client_business: Optional[str] = None     # 발주처 주요 업종
    industry_code: Optional[str] = None       # 산업군 코드
    investment_type: Optional[str] = None     # 신설 / 증설 / 이전 / 매입 등
    amount_billion_krw: Optional[float] = None  # 예상 규모 (억원)
    location: Optional[str] = None            # 사업 위치
    land_area_m2: Optional[float] = None      # 대지면적
    total_floor_area_m2: Optional[float] = None  # 연면적
    building_scale: Optional[str] = None      # 지하N층~지상N층
    cm_company: Optional[str] = None          # CM사
    designer: Optional[str] = None            # 설계사
    schedule: Optional[str] = None            # 사업일정
    summary: Optional[str] = None             # 한 줄 요약
