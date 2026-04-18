FROM node:18

WORKDIR /app
COPY package*.json ./
RUN npm install

COPY . .
EXPOSE 3000
# --max-old-space-size: cap V8 heap di 350 MB (di bawah container limit 512 MB)
# Sehingga Node.js throw JS OOM error yang bisa di-catch, bukan di-kill oleh kernel
CMD ["node", "--max-old-space-size=350", "index.js"]
