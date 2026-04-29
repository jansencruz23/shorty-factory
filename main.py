import sys

import uvicorn


def main():
    # On Windows, uvicorn's reload mode forces SelectorEventLoop, which has
    # no subprocess transport — that breaks Playwright (it spawns a Node
    # driver). Disable reload on Windows; restart the process manually.
    reload = sys.platform != "win32"
    uvicorn.run("app.api.main:app", host="0.0.0.0", port=8000, reload=reload)


if __name__ == "__main__":
    main()
