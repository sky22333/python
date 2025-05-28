### 自用python工具合集


- Docker启用python容器
```
docker run -d --name py \
  -v /home:/home \
  python:3.12-slim \
  tail -f /dev/null
```

- 运行指定脚本
```
python test.py
```
