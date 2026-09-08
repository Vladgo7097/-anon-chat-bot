import asyncio
import json
import logging
import time
from datetime import datetime, timezone
from aiogram import BaseMiddleware


class JsonFormatter(logging.Formatter):
    def format(self,record):
        payload={"timestamp":datetime.fromtimestamp(record.created,timezone.utc).isoformat(),
            "level":record.levelname,"logger":record.name,"event":getattr(record,"event",record.getMessage())}
        for field in ("user_id","anon_id","chat_session_id","payment_id","report_id","duration_ms","result","error_code"):
            if hasattr(record,field):
                payload[field]=getattr(record,field)
        # logging.exception()/exc_info=True outside an active except block
        # resolves to (None, None, None) via sys.exc_info() -- still truthy.
        if record.exc_info and record.exc_info[0]:
            payload["error_code"]=record.exc_info[0].__name__
        return json.dumps(payload,ensure_ascii=False)


class UpdateLoggingMiddleware(BaseMiddleware):
    async def __call__(self,handler,event,data):
        started=time.monotonic()
        result,error="ok",None
        try:
            return await handler(event,data)
        except asyncio.CancelledError:
            result,error="cancelled","CancelledError"
            raise
        except Exception as exc:
            result,error="failed",type(exc).__name__
            raise
        finally:
            actor=getattr(event,"from_user",None)
            logging.getLogger("updates").info("update",extra={"event":"update_processed",
                "user_id":actor.id if actor else None,"anon_id":data.get("anon_id"),"duration_ms":round((time.monotonic()-started)*1000),"result":result,"error_code":error})
