import re
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

LOGIN_ID_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9._-]{2,18}[a-z0-9])?$")
PASSWORD_PATTERN = re.compile(r"^(?=.*[A-Za-z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,128}$")


class AuthSignupRequest(BaseModel):
    login_id: str = Field(..., min_length=4, max_length=20)
    password: str = Field(..., min_length=8, max_length=128)
    signup_code: str = Field(..., min_length=1, max_length=64)
    name: str | None = Field(default=None, max_length=100)

    @field_validator("login_id")
    @classmethod
    def validate_login_id(cls, value: str) -> str:
        login_id = value.strip().lower()
        if not LOGIN_ID_PATTERN.fullmatch(login_id):
            raise ValueError(
                "아이디는 4~20자의 영문 소문자, 숫자, ., _, - 만 사용할 수 있습니다."
            )
        return login_id

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if not PASSWORD_PATTERN.fullmatch(value):
            raise ValueError(
                "비밀번호는 8자 이상이며 영문/숫자/특수문자를 각각 포함해야 합니다."
            )
        return value

    @field_validator("signup_code")
    @classmethod
    def validate_signup_code(cls, value: str) -> str:
        return value.strip()


class AuthLoginRequest(BaseModel):
    login_id: str = Field(..., min_length=4, max_length=20)
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("login_id")
    @classmethod
    def validate_login_id(cls, value: str) -> str:
        login_id = value.strip().lower()
        if not LOGIN_ID_PATTERN.fullmatch(login_id):
            raise ValueError(
                "아이디는 4~20자의 영문 소문자, 숫자, ., _, - 만 사용할 수 있습니다."
            )
        return login_id


class AuthUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    login_id: str = Field(validation_alias="email")
    name: str | None
    is_active: bool
    is_super: bool
    created_at: datetime


class AuthSessionResponse(BaseModel):
    user: AuthUserResponse


class AuthLoginIdCheckResponse(BaseModel):
    login_id: str
    available: bool
