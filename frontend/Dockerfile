# Step 1: Lightweight Node environment base build
FROM node:20-alpine AS base
WORKDIR /app
COPY package*.json ./
RUN npm ci --only=production
COPY server.js ./

# Step 2: Clean runtime environment execution
FROM node:20-alpine AS runner
WORKDIR /app
COPY --from=base /app ./

EXPOSE 3000
ENV NODE_ENV=production
ENV PORT=3000
CMD ["npm", "start"]