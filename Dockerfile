FROM python:3.11-slim

WORKDIR /app

RUN apt-get update -y
RUN apt-get install -y gcc curl sed git
RUN curl -sSL https://install.python-poetry.org | python3 -
ENV PATH="/root/.local/bin:$PATH"

ADD . /app/
RUN poetry update

EXPOSE 8080
CMD ["python", "/app/bot.py"]