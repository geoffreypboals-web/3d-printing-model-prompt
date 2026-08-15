FROM node:20-alpine AS build
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm install
COPY tsconfig.json ./
COPY src ./src
RUN npm run build

FROM node:20-bookworm-slim
WORKDIR /app
ENV NODE_ENV=production
ENV DEBIAN_FRONTEND=noninteractive

# Blender powers the wall-thickness feature (Solidify modifier + 3D Print
# Toolbox thickness analysis). python3-numpy is required by Blender's glTF
# exporter (not bundled with the apt package). libegl1/libgl1-mesa-dri/
# libglx-mesa0 let headless rendering fall back to software (surfaceless
# EGL) rendering without a GPU.
RUN apt-get update \
  && apt-get install -y --no-install-recommends \
    blender \
    python3-numpy \
    libegl1 \
    libgl1-mesa-dri \
    libglx-mesa0 \
  && rm -rf /var/lib/apt/lists/*

COPY package.json package-lock.json* ./
RUN npm install --omit=dev
COPY --from=build /app/dist ./dist
COPY public ./public
COPY scripts ./scripts

EXPOSE 4100
CMD ["node", "dist/server.js"]
