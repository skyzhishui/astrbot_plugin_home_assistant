"""
Home Assistant 智能家居控制插件
"""

import aiohttp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star


class Main(Star):
    """Home Assistant 智能家居控制插件"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.context = context
        # 默认配置
        self.ha_url = "http://127.0.0.1:8123"
        self.ha_token = "token"
        self.timeout = 10
        self.enable_light_commands = True
        self.enable_switch_commands = True
        self.enable_llm_tools = True
        
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def initialize(self):
        try:
            config = self.config
            self.ha_url = config.get("ha_url", self.ha_url)
            self.ha_token = config.get("ha_token", self.ha_token)
            self.timeout = config.get("timeout", self.timeout)
            self.enable_light_commands = config.get("enable_light_commands", True)
            self.enable_switch_commands = config.get("enable_switch_commands", True)
            self.enable_llm_tools = config.get("enable_llm_tools", True)
        except Exception as e:
            logger.warning(f"[HomeAssistant] 加载配置失败: {e}")
        logger.info(f"[HomeAssistant] 插件初始化完成，HA: {self.ha_url}")

    async def terminate(self):
        if self._session and not self._session.closed:
            await self._session.close()

    async def _ha_request(self, endpoint: str, method: str = "GET", data: dict | None = None) -> dict | None:
        session = await self._get_session()
        url = f"{self.ha_url.rstrip('/')}/api{endpoint}"
        headers = {
            "Authorization": f"Bearer {self.ha_token}",
            "Content-Type": "application/json",
        }
        try:
            async with session.request(method, url, headers=headers, json=data, timeout=aiohttp.ClientTimeout(total=self.timeout)) as resp:
                if resp.status == 200:
                    if resp.content_length and resp.content_length > 0:
                        return await resp.json()
                    return {"success": True}
                else:
                    logger.error(f"[HomeAssistant] API 错误: {resp.status}")
                    return None
        except Exception as e:
            logger.error(f"[HomeAssistant] API 异常: {e}")
            return None

    async def _get_all_entities(self) -> list[dict]:
        result = await self._ha_request("/states")
        return result if result else []

    # ===== 灯光 =====
    async def _get_lights(self) -> list[dict]:
        entities = await self._get_all_entities()
        return [{"entity_id": e["entity_id"], "state": e.get("state", "unknown"),
                 "name": e.get("attributes", {}).get("friendly_name", e["entity_id"]),
                 "brightness": e.get("attributes", {}).get("brightness", 0)}
                for e in entities if e.get("entity_id", "").startswith("light.")]

    async def _turn_light(self, entity_id: str, action: str, brightness: int | None = None) -> bool:
        service = "turn_on" if action == "on" else "turn_off"
        data = {"entity_id": entity_id}
        if action == "on" and brightness is not None:
            data["brightness"] = max(0, min(255, brightness))
        result = await self._ha_request(f"/services/light/{service}", "POST", data)
        return result is not None

    # ===== 开关 =====
    async def _get_switches(self) -> list[dict]:
        entities = await self._get_all_entities()
        return [{"entity_id": e["entity_id"], "state": e.get("state", "unknown"),
                 "name": e.get("attributes", {}).get("friendly_name", e["entity_id"])}
                for e in entities if e.get("entity_id", "").startswith("switch.") or e.get("entity_id", "").startswith("input_boolean.")]

    async def _turn_switch(self, entity_id: str, action: str) -> bool:
        service = "turn_on" if action == "on" else "turn_off"
        if entity_id.startswith("switch."):
            endpoint = f"/services/switch/{service}"
        elif entity_id.startswith("input_boolean."):
            endpoint = f"/services/input_boolean/{service}"
        else:
            endpoint = f"/services/homeassistant/{service}"
        result = await self._ha_request(endpoint, "POST", {"entity_id": entity_id})
        return result is not None

    # ===== 查找 =====
    async def _find_entity(self, name: str, entity_type: str = "all") -> dict | None:
        entities = await self._get_all_entities()
        name_lower = name.lower()
        candidates = []
        for e in entities:
            eid = e.get("entity_id", "")
            fname = e.get("attributes", {}).get("friendly_name", "")
            if entity_type == "light" and not eid.startswith("light."):
                continue
            if entity_type == "switch" and not (eid.startswith("switch.") or eid.startswith("input_boolean.")):
                continue
            if name_lower in fname.lower() or name_lower in eid.lower():
                candidates.append({"entity_id": eid, "state": e.get("state", "unknown"),
                                   "name": fname or eid, "brightness": e.get("attributes", {}).get("brightness", 0)})
        for c in candidates:
            if name_lower == c["name"].lower() or name_lower == c["entity_id"].lower():
                return c
        return candidates[0] if candidates else None

    # ===== 命令: 灯光 =====
    @filter.command("灯光列表", alias={"灯列表"})
    async def list_lights(self, event: AstrMessageEvent):
        if not self.enable_light_commands:
            yield event.plain_result("💡 灯光命令已禁用")
            return
        lights = await self._get_lights()
        if not lights:
            yield event.plain_result("💡 未找到灯光设备")
            return
        msg = "💡 灯光设备列表\n━━━━━━━━━━━━━━\n"
        for l in lights:
            emoji = "🟢" if l["state"] == "on" else "⚫"
            bright = f" ({round(l['brightness']/255*100)}%)" if l["state"] == "on" and l["brightness"] else ""
            msg += f"{emoji} {l['name']}{bright}\n"
        yield event.plain_result(msg)

    @filter.command("开灯")
    async def cmd_open_light(self, event: AstrMessageEvent, light_name: str = ""):
        if not self.enable_light_commands:
            yield event.plain_result("💡 灯光命令已禁用")
            return
        if not light_name:
            yield event.plain_result("用法: /开灯 <灯光名称>")
            return
        target = await self._find_entity(light_name, "light")
        if not target:
            yield event.plain_result(f"未找到灯光: {light_name}")
            return
        if await self._turn_light(target["entity_id"], "on"):
            yield event.plain_result(f"💡 已打开: {target['name']}")
        else:
            yield event.plain_result(f"❌ 打开失败")

    @filter.command("关灯")
    async def cmd_close_light(self, event: AstrMessageEvent, light_name: str = ""):
        if not self.enable_light_commands:
            yield event.plain_result("💡 灯光命令已禁用")
            return
        if not light_name:
            yield event.plain_result("用法: /关灯 <灯光名称>")
            return
        target = await self._find_entity(light_name, "light")
        if not target:
            yield event.plain_result(f"未找到灯光: {light_name}")
            return
        if await self._turn_light(target["entity_id"], "off"):
            yield event.plain_result(f"⚫ 已关闭: {target['name']}")
        else:
            yield event.plain_result(f"❌ 关闭失败")

    # ===== 命令: 开关 =====
    @filter.command("开关列表", alias={"插座列表"})
    async def list_switches(self, event: AstrMessageEvent):
        if not self.enable_switch_commands:
            yield event.plain_result("🔌 开关命令已禁用")
            return
        switches = await self._get_switches()
        if not switches:
            yield event.plain_result("🔌 未找到开关设备")
            return
        msg = "🔌 开关设备列表\n━━━━━━━━━━━━━━\n"
        for s in switches:
            emoji = "🟢" if s["state"] == "on" else "⚫"
            msg += f"{emoji} {s['name']}\n"
        yield event.plain_result(msg)

    @filter.command("打开")
    async def cmd_open_switch(self, event: AstrMessageEvent, switch_name: str = ""):
        if not self.enable_switch_commands:
            yield event.plain_result("🔌 开关命令已禁用")
            return
        if not switch_name:
            yield event.plain_result("用法: /打开 <开关名称>")
            return
        target = await self._find_entity(switch_name, "switch")
        if not target:
            yield event.plain_result(f"未找到开关: {switch_name}")
            return
        if await self._turn_switch(target["entity_id"], "on"):
            yield event.plain_result(f"🟢 已打开: {target['name']}")
        else:
            yield event.plain_result(f"❌ 打开失败")

    @filter.command("关闭")
    async def cmd_close_switch(self, event: AstrMessageEvent, switch_name: str = ""):
        if not self.enable_switch_commands:
            yield event.plain_result("🔌 开关命令已禁用")
            return
        if not switch_name:
            yield event.plain_result("用法: /关闭 <开关名称>")
            return
        target = await self._find_entity(switch_name, "switch")
        if not target:
            yield event.plain_result(f"未找到开关: {switch_name}")
            return
        if await self._turn_switch(target["entity_id"], "off"):
            yield event.plain_result(f"⚫ 已关闭: {target['name']}")
        else:
            yield event.plain_result(f"❌ 关闭失败")

    @filter.command("设备列表", alias={"智能家居"})
    async def list_all(self, event: AstrMessageEvent):
        lights = await self._get_lights() if self.enable_light_commands else []
        switches = await self._get_switches() if self.enable_switch_commands else []
        msg = "🏠 智能家居设备\n━━━━━━━━━━━━━━\n"
        if lights:
            msg += "💡 灯光:\n"
            for l in lights:
                emoji = "🟢" if l["state"] == "on" else "⚫"
                msg += f"  {emoji} {l['name']}\n"
        if switches:
            msg += "🔌 开关:\n"
            for s in switches:
                emoji = "🟢" if s["state"] == "on" else "⚫"
                msg += f"  {emoji} {s['name']}\n"
        yield event.plain_result(msg)

    # ===== LLM 工具 =====
    @filter.llm_tool(name="homeassistant_turn_on_light")
    async def llm_light_on(self, event: AstrMessageEvent, light_name: str):
        """打开灯光。Args: light_name(string): 灯光名称"""
        if not self.enable_llm_tools:
            return "LLM工具已禁用"
        target = await self._find_entity(light_name, "light")
        if not target:
            return f"未找到: {light_name}"
        if await self._turn_light(target["entity_id"], "on"):
            return f"已打开 {target['name']}"
        return "操作失败"

    @filter.llm_tool(name="homeassistant_turn_off_light")
    async def llm_light_off(self, event: AstrMessageEvent, light_name: str):
        """关闭灯光。Args: light_name(string): 灯光名称"""
        if not self.enable_llm_tools:
            return "LLM工具已禁用"
        target = await self._find_entity(light_name, "light")
        if not target:
            return f"未找到: {light_name}"
        if await self._turn_light(target["entity_id"], "off"):
            return f"已关闭 {target['name']}"
        return "操作失败"

    @filter.llm_tool(name="homeassistant_turn_on_switch")
    async def llm_switch_on(self, event: AstrMessageEvent, switch_name: str):
        """打开开关/插座。Args: switch_name(string): 开关名称"""
        if not self.enable_llm_tools:
            return "LLM工具已禁用"
        target = await self._find_entity(switch_name, "switch")
        if not target:
            return f"未找到: {switch_name}"
        if await self._turn_switch(target["entity_id"], "on"):
            return f"已打开 {target['name']}"
        return "操作失败"

    @filter.llm_tool(name="homeassistant_turn_off_switch")
    async def llm_switch_off(self, event: AstrMessageEvent, switch_name: str):
        """关闭开关/插座。Args: switch_name(string): 开关名称"""
        if not self.enable_llm_tools:
            return "LLM工具已禁用"
        target = await self._find_entity(switch_name, "switch")
        if not target:
            return f"未找到: {switch_name}"
        if await self._turn_switch(target["entity_id"], "off"):
            return f"已关闭 {target['name']}"
        return "操作失败"

    @filter.llm_tool(name="homeassistant_get_lights")
    async def llm_get_lights(self, event: AstrMessageEvent):
        """获取灯光列表。"""
        lights = await self._get_lights()
        if not lights:
            return "没有灯光设备"
        return "\n".join([f"{l['name']}: {'开启' if l['state']=='on' else '关闭'}" for l in lights])

    @filter.llm_tool(name="homeassistant_get_switches")
    async def llm_get_switches(self, event: AstrMessageEvent):
        """获取开关列表。"""
        switches = await self._get_switches()
        if not switches:
            return "没有开关设备"
        return "\n".join([f"{s['name']}: {'开启' if s['state']=='on' else '关闭'}" for s in switches])
