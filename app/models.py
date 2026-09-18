from pydantic import BaseModel, Field

class ResetRequest(BaseModel):
    ticket_count: int = Field(ge=0, le=1_000_000)

class BuyRequest(BaseModel):
    user_id: str = Field(min_length=1, max_length=200)
    request_id: str = Field(min_length=1, max_length=200)
