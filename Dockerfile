# Backend image: FastAPI + MuJoCo (CPU rendering through OSMesa) + ffmpeg.
FROM python:3.11-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg libosmesa6 libgl1 git ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ENV MUJOCO_GL=osmesa \
    PYOPENGL_PLATFORM=osmesa \
    PYTHONUNBUFFERED=1 \
    SHOWTELL_DATA_DIR=/data

WORKDIR /app
COPY requirements.txt pyproject.toml ./
RUN pip install --no-cache-dir -r requirements.txt

# The Franka Panda model from MuJoCo Menagerie, pinned to the commit the project was built with.
ARG MENAGERIE_COMMIT=b0655057a9df2abe39ea7fc989914ba6f248301c
RUN git clone --filter=blob:none https://github.com/google-deepmind/mujoco_menagerie /tmp/menagerie \
    && git -C /tmp/menagerie checkout "$MENAGERIE_COMMIT" \
    && mkdir -p third_party/mujoco_menagerie \
    && cp -r /tmp/menagerie/franka_emika_panda third_party/mujoco_menagerie/ \
    && rm -rf /tmp/menagerie

COPY showtell ./showtell
COPY bench ./bench
COPY examples ./examples
RUN pip install --no-cache-dir --no-deps -e .

VOLUME ["/data"]
EXPOSE 8000
CMD ["uvicorn", "showtell.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
