import json
import anyio
import uvicorn
from uuid import uuid4
from pydantic import BaseModel, RootModel
from typing import Dict, Literal
from starlette.applications import Starlette
from starlette.responses import JSONResponse, StreamingResponse, HTMLResponse
from starlette.routing import Route
from starlette.requests import Request
from anyio.streams.memory import MemoryObjectSendStream


class ListResourcesRequest(BaseModel):
    requestid: str
    method: Literal["resources/list"]
    params: dict | None = None

class PingRequest(BaseModel):
    requestid: str
    method: Literal["ping"]
    params: dict | None = None

class ListToolsRequest(BaseModel):
    requestid: str
    method: Literal["tools/list"]
    params: dict | None = None

class ClientRequest(RootModel[PingRequest | ListResourcesRequest | ListToolsRequest]):
    pass


# { sessionid: writer }
session2writer: Dict[str, MemoryObjectSendStream[ClientRequest]] = {}

def handle_client_request(data: ClientRequest):
    match data.root:
        case PingRequest():
            return 'this is a ping request'
        case ListResourcesRequest():
            return 'this is a list resources request'
        case ListToolsRequest():
            return 'this is a list tools request'
        case _:
            return 'unknown request'

async def endpoint_sse(request: Request):
    """
    1. 客户端连接时, 服务器会立即发送一条消息, 告知客户端 sessionid
    2. 监听客户端消息, 不同的消息类型不同的逻辑, 然后使用 yield 将结果推送给客户端
    3. 如何监听客户端消息？客户端POST消息到 /message 接口, 服务器使用 StreamingResponse 监听这个接口的消息
    """
    sessionid = uuid4()
    
    w, r = anyio.create_memory_object_stream[ClientRequest](max_buffer_size=1)
    session2writer[sessionid.hex] = w

    async def content():
        # 立即发送一条消息, 告知客户端 sessionid
        data = json.dumps({"event": "endpoint", "data": f"/message?sessionid={sessionid.hex}"})
        yield f"data: {data}\n\n"

        # 监听客户端消息, 不同的消息类型不同的逻辑
        async for msg in r:
            if msg is None:
                break
            res = handle_client_request(msg)
            yield f"data: {res}\n\n"

    return StreamingResponse(content(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

async def endpoint_message(request: Request):
    """
    1. 客户端通过POST消息到 /message 接口来与服务器进行交互
    """
    sessionid = request.query_params.get("sessionid", None)
    if sessionid is None:
        return JSONResponse({"error": "sessionid is required"}, status_code=400)
    w = session2writer.get(sessionid, None)
    if w is None:
        return JSONResponse({"error": "sessionid not found"}, status_code=404)
    data = await request.body()
    if not data:
        return JSONResponse({"error": "data is required"}, status_code=400)
    try:
        obj = ClientRequest.model_validate_json(data.decode('utf-8'))
        await w.send(obj)
        return JSONResponse({"status": "ok"}, status_code=200)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=400)

async def endpoint_index(request: Request):
    with open("index.html", "r", encoding='utf-8') as f:
        content = f.read()
    return HTMLResponse(content, media_type="text/html")

def server():
    routes = [
        Route("/sse", endpoint_sse, methods=["GET"]),
        Route("/message", endpoint_message, methods=["POST"]),
        Route("/", endpoint_index, methods=["GET"]),
    ]
    s = Starlette(routes=routes)
    uvicorn.run(s, host="127.0.0.1", port=8001)

if __name__ == "__main__":
    server()

