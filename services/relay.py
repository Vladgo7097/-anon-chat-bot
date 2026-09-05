from services.chats import ChatService
from services.result import ServiceResult
from repositories.chats import ChatRepository
from services.growth import track


class RelayService:
    SUPPORTED={"text","photo","voice","sticker","video","video_note","animation"}

    async def prepare(self,user_id,content_type):
        chat=await ChatService().active(user_id)
        if not chat:
            return ServiceResult(False,"NOT_CHATTING")
        if content_type not in self.SUPPORTED:
            return ServiceResult(False,"UNSUPPORTED_CONTENT")
        partner=chat.user2_id if chat.user1_id==user_id else chat.user1_id
        return ServiceResult(True,"RELAY",{"partner":partner,"session_id":chat.session_id})

    async def delivered(self,user_id,session_id,source_chat,message_id):
        await ChatRepository().touch(session_id)
        await track(user_id,"message_relayed",f"relay:{source_chat}:{message_id}")
