FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY bot.py crypto.py checkout.py railway.py config.example.json ./
COPY assets ./assets
COPY checkout ./checkout
CMD ["python", "-u", "railway.py"]
