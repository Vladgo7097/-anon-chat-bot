from datetime import timedelta
from sqlalchemy import select, func, or_, update
from models.base import async_session
from models.user import User
from models.chat import Chat
from models.report import Report
from models.ban import Ban
from models.payment import Payment, Product, PremiumSubscription
from models.operations import AdminAudit, BotSetting, Broadcast, AnalyticsEvent, Offer
from services.result import ServiceResult
from services.chats import ChatService
from services.matching import MatchingService
from utils.time import utcnow
import config


def authorize(admin_id):
    if admin_id not in config.ADMIN_IDS:
        raise PermissionError("Admin access required")


def audit(s, admin, action, target_type, target_id, before=None, after=None,request_key=None):
    s.add(AdminAudit(admin_id=admin, action=action, target_type=target_type, target_id=str(target_id), before_json=before, after_json=after,request_key=request_key))


class StatsService:
    async def retention(self,s,days):
        history=await s.scalar(select(func.min(AnalyticsEvent.created_at)).where(AnalyticsEvent.event=="daily_active"))
        if not history:
            return None
        cutoff=utcnow().replace(hour=0,minute=0,second=0,microsecond=0)-timedelta(days=days)
        cohort=(User.created_at>=history,User.created_at<cutoff)
        total=await s.scalar(select(func.count(User.id)).where(*cohort))
        if not total:
            return None
        target=func.date(User.created_at)+days if s.bind.dialect.name=="postgresql" else func.date(User.created_at,f"+{days} day")
        returned=await s.scalar(select(func.count(func.distinct(User.id))).join(AnalyticsEvent,AnalyticsEvent.user_id==User.telegram_id).where(*cohort,AnalyticsEvent.event=="daily_active",func.date(AnalyticsEvent.created_at)==target))
        return round(100*returned/total,1)

    async def overview(self, days=1):
        cutoff=utcnow().replace(hour=0,minute=0,second=0,microsecond=0)-timedelta(days=days-1)
        async with async_session() as s:
            users=await s.scalar(select(func.count(User.id)))
            new=await s.scalar(select(func.count(User.id)).where(User.created_at>=cutoff))
            chats=await s.scalar(select(func.count(Chat.id)).where(Chat.status=="ended",Chat.ended_at>=cutoff))
            duration=await s.scalar(select(func.avg(Chat.duration_seconds)).where(Chat.status=="ended",Chat.ended_at>=cutoff))
            stars=await s.scalar(select(func.coalesce(func.sum(Payment.stars_amount),0)).where(Payment.status=="paid",Payment.paid_at>=cutoff))
            premium=await s.scalar(select(func.count(User.id)).where(User.premium_expires>utcnow()))
            products=(await s.execute(select(Payment.product,func.sum(Payment.stars_amount)).where(Payment.status=="paid",Payment.paid_at>=cutoff).group_by(Payment.product))).all()
            funnel=dict((await s.execute(select(AnalyticsEvent.event,func.count(func.distinct(AnalyticsEvent.user_id))).where(AnalyticsEvent.created_at>=cutoff).group_by(AnalyticsEvent.event))).all())
            rating=await s.scalar(select(func.avg(User.positive_rating_pct)).where(User.likes_received+User.dislikes_received>0))
            d1,d7=await self.retention(s,1),await self.retention(s,7)
        return dict(users=users,new=new,chats=chats,avg_seconds=round(duration or 0),stars=stars,premium=premium,products=dict(products),funnel=funnel,rating=round(rating,1) if rating is not None else None,retention_d1=d1,retention_d7=d7)


class AdminService:
    async def seasonal(self,admin,code,starts_at,ends_at,discount):
        authorize(admin)
        from datetime import datetime
        import re
        try:
            start,end=datetime.fromisoformat(starts_at),datetime.fromisoformat(ends_at)
            valid=start.tzinfo is not None and end.tzinfo is not None and start<end and end>utcnow()
        except (ValueError,TypeError):
            valid=False
        if not valid or not re.fullmatch(r"[a-z0-9_]{1,32}",code) or type(discount) is not int or not 1<=discount<=90:
            return ServiceResult(False,"INVALID")
        async with async_session.begin() as s:
            from repositories.chats import lock_matching
            await lock_matching(s)
            item=await s.get(BotSetting,"seasonal_event",with_for_update=True)
            previous=item.value_json if item else None
            if not item:
                item=BotSetting(key="seasonal_event")
                s.add(item)
            item.value_json={"code":code,"starts_at":start.isoformat(),"ends_at":end.isoformat(),"discount":discount}
            item.updated_at,item.updated_by=utcnow(),admin
            audit(s,admin,"seasonal","setting","seasonal_event",previous,item.value_json)
        return ServiceResult(True,"SAVED")

    async def reports(self, admin, offset=0):
        authorize(admin)
        async with async_session() as s:
            return list((await s.scalars(select(Report).where(Report.status=="pending").order_by(Report.id).offset(offset).limit(10))).all())

    async def report_details(self,admin,report):
        authorize(admin)
        async with async_session() as s:
            users=(await s.scalars(select(User).where(User.telegram_id.in_([report.reporter_id,report.reported_id])))).all()
            names={u.telegram_id:u.anon_id for u in users}
            previous=await s.scalar(select(func.count(Report.id)).where(Report.reported_id==report.reported_id,Report.id!=report.id))
            bans=await s.scalar(select(func.count(Ban.id)).where(Ban.user_id==report.reported_id))
            return names,previous,bans

    async def resolve(self, admin, report_id, action):
        authorize(admin)
        if action not in {"ban1", "banf", "dismiss"}:
            return ServiceResult(False,"INVALID")
        async with async_session.begin() as s:
            from repositories.chats import lock_matching
            await lock_matching(s)
            report=await s.get(Report,report_id,with_for_update=True)
            if not report or report.status!="pending":
                return ServiceResult(False,"ALREADY_RESOLVED")
            report.status="dismissed" if action=="dismiss" else "resolved"
            report.resolved_at,report.resolved_by=utcnow(),admin
            if action!="dismiss":
                await self._ban(s,admin,report.reported_id,report.reason,24 if action=="ban1" else None,report.id)
            audit(s,admin,action,"report",report_id,{"status":"pending"},{"status":report.status})
            uid=report.reported_id
        if action!="dismiss":
            await MatchingService().cancel(uid)
            await ChatService().end_chat(uid,reason="ban")
        return ServiceResult(True,"RESOLVED")

    async def _ban(self,s,admin,uid,reason,hours,report_id=None):
        user=await s.scalar(select(User).where(User.telegram_id==uid).with_for_update())
        if not user:
            raise ValueError("User not found")
        expires=utcnow()+timedelta(hours=hours) if hours else None
        await s.execute(update(Ban).where(Ban.user_id==uid, Ban.is_active.is_(True)).values(is_active=False))
        user.is_banned,user.ban_expires=True,expires
        record=Ban(user_id=uid,reason=reason[:64],duration_hours=hours,expires_at=expires,banned_by=admin,report_id=report_id)
        s.add(record)
        await s.flush()
        from services.notifications import enqueue
        until=f"до {expires:%d.%m.%Y %H:%M} UTC" if expires else "бессрочно"
        await enqueue(s,uid,"ban",f"ban:{record.id}",f"⛔ Ты заблокирован {until}. Причина: {reason[:500]}")

    async def find_user(self,admin,value):
        authorize(admin)
        async with async_session() as s:
            q=select(User).where(or_(User.anon_id==value,User.telegram_id==(int(value) if value.isdigit() else -1)))
            return await s.scalar(q)

    async def ban(self,admin,uid,reason,hours=None):
        authorize(admin)
        if not reason or (hours is not None and not 1<=hours<=87600):
            return ServiceResult(False,"INVALID")
        async with async_session.begin() as s:
            from repositories.chats import lock_matching
            await lock_matching(s)
            await self._ban(s,admin,uid,reason,hours)
            audit(s,admin,"ban","user",uid,after={"reason":reason,"hours":hours})
        await MatchingService().cancel(uid)
        await ChatService().end_chat(uid,reason="ban")
        return ServiceResult(True,"BANNED")

    async def unban(self,admin,uid):
        authorize(admin)
        async with async_session.begin() as s:
            user=await s.scalar(select(User).where(User.telegram_id==uid).with_for_update())
            if not user:
                return ServiceResult(False,"NOT_FOUND")
            before={"is_banned":user.is_banned}
            user.is_banned,user.ban_expires=False,None
            await s.execute(update(Ban).where(Ban.user_id==uid, Ban.is_active.is_(True)).values(is_active=False))
            if before["is_banned"]:
                from services.notifications import enqueue
                await enqueue(s,uid,"unban",f"unban:{uid}:{utcnow().isoformat()}","<tg-emoji emoji-id=\"5280880410346152584\">✅</tg-emoji> Блокировка снята, добро пожаловать обратно!", "menu")
            audit(s,admin,"unban","user",uid,before,{"is_banned":False})
        return ServiceResult(True,"UNBANNED")

    async def bans(self,admin):
        authorize(admin)
        async with async_session() as s:
            return list((await s.scalars(select(User).where(User.is_banned.is_(True),or_(User.ban_expires.is_(None),User.ban_expires>utcnow())).limit(50))).all())

    async def grant(self,admin,uid,days,reason,request_key=None):
        authorize(admin)
        if not 1<=days<=3650 or not reason:
            return ServiceResult(False,"INVALID")
        async with async_session.begin() as s:
            user=await s.scalar(select(User).where(User.telegram_id==uid).with_for_update())
            if not user:
                return ServiceResult(False,"NOT_FOUND")
            if request_key and await s.scalar(select(AdminAudit.id).where(AdminAudit.request_key==request_key)):
                return ServiceResult(False,"ALREADY_GRANTED")
            old=user.premium_expires
            user.premium_expires=max(utcnow(),old or utcnow())+timedelta(days=days)
            user.is_premium=True
            s.add(PremiumSubscription(user_id=uid,plan="admin",stars_paid=0,expires_at=user.premium_expires))
            audit(s,admin,"grant_premium","user",uid,{"expires":old.isoformat() if old else None},{"expires":user.premium_expires.isoformat(),"reason":reason},request_key=request_key)
        return ServiceResult(True,"GRANTED")

    async def price(self,admin,code,stars):
        authorize(admin)
        if not 1<=stars<=100000:
            return ServiceResult(False,"INVALID")
        async with async_session.begin() as s:
            p=await s.get(Product,code,with_for_update=True)
            if not p:
                return ServiceResult(False,"NOT_FOUND")
            before=p.price_stars
            p.price_stars,p.updated_at=stars,utcnow()
            audit(s,admin,"price","product",code,{"price":before},{"price":stars})
        return ServiceResult(True,"SAVED")

    async def setting(self,admin,key,value):
        authorize(admin)
        limits={"chat_delay_next":(0,60),"search_timeout":(30,3600),"inactive_chat_ttl":(60,86400),"marketing_enabled":(0,1),"seasonal_enabled":(0,1)}
        if type(value) is not int or key not in limits or not limits[key][0]<=value<=limits[key][1]:
            return ServiceResult(False,"INVALID")
        async with async_session.begin() as s:
            setting=await s.get(BotSetting,key,with_for_update=True)
            before=setting.value_json if setting else None
            if not setting:
                setting=BotSetting(key=key)
                s.add(setting)
            setting.value_json,setting.updated_at,setting.updated_by={"value":value},utcnow(),admin
            audit(s,admin,"setting","setting",key,before,{"value":value})
        return ServiceResult(True,"SAVED")

    async def broadcast(self,admin,segment,content):
        authorize(admin)
        if segment not in {"all","premium","inactive","nopay"} or not 1<=len(content)<=4000:
            return ServiceResult(False,"INVALID")
        async with async_session.begin() as s:
            job=Broadcast(admin_id=admin,segment=segment,content=content)
            s.add(job)
            await s.flush()
            audit(s,admin,"broadcast_draft","broadcast",job.id,after={"segment":segment})
        return ServiceResult(True,"DRAFT",{"job":job})

    async def confirm_broadcast(self,admin,job_id):
        authorize(admin)
        async with async_session.begin() as s:
            job=await s.get(Broadcast,job_id,with_for_update=True)
            if not job or job.admin_id!=admin or job.status!="draft":
                return ServiceResult(False,"ALREADY_STARTED")
            job.status,job.started_at="queued",utcnow()
            audit(s,admin,"broadcast_confirm","broadcast",job.id,after={"status":"queued"})
        return ServiceResult(True,"QUEUED")

    async def send_message_to_user(self,admin,target_id,text,request_key=None):
        authorize(admin)
        if not text or len(text)>4000:
            return ServiceResult(False,"INVALID")
        async with async_session.begin() as s:
            user=await s.scalar(select(User).where(User.telegram_id==target_id).with_for_update())
            if not user:
                return ServiceResult(False,"NOT_FOUND")
            if request_key and await s.scalar(select(AdminAudit.id).where(AdminAudit.request_key==request_key)):
                return ServiceResult(False,"ALREADY_SENT")
            from services.notifications import enqueue
            await enqueue(s,target_id,"admin_message",f"admin_msg:{request_key}",text,None)
            audit(s,admin,"send_message","user",target_id,after={"text":text[:200]},request_key=request_key)
        return ServiceResult(True,"SENT",{"user":user})

    async def broadcast_status(self,admin,job_id=None):
        authorize(admin)
        async with async_session() as s:
            if job_id:
                job=await s.get(Broadcast,job_id)
                if not job or job.admin_id!=admin:
                    return None
                return job
            jobs=list((await s.scalars(select(Broadcast).where(Broadcast.admin_id==admin).order_by(Broadcast.id.desc()).limit(10))).all())
            return jobs
