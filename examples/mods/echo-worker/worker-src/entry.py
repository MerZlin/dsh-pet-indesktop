from pet.mod_api.worker_v1 import serve


def echo(operation, arguments, cancel):
    if cancel.is_set():
        return {}
    if operation != "echo":
        return {"error": "unsupported_operation"}
    return {"echo": str(arguments.get("text", ""))[:4096]}


if __name__ == "__main__":
    raise SystemExit(serve("demo.echo-worker", echo))
