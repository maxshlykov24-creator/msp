FROM python:3.12-slim
WORKDIR /app
COPY app/main.py /app/main.py
ENV PORT=8080
EXPOSE 8080
CMD ["python3", "-u", "/app/main.py"]
