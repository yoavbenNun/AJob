"""Dedicated web process; imports no scanner entry point or scheduler."""
import os
import uvicorn


def main():
    uvicorn.run('src.web.cloud:create_cloud_app', factory=True,
                host='0.0.0.0', port=int(os.environ.get('PORT', '10000')),
                workers=1, proxy_headers=False, access_log=False, server_header=False)


if __name__ == '__main__':
    main()
