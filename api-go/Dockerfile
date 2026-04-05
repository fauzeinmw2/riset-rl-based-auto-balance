# Stage 1: Build
FROM golang:1.24-alpine AS builder
WORKDIR /app

# Install git untuk download modul jika diperlukan
RUN apk add --no-cache git

# Copy dependensi
COPY go.mod go.sum ./
RUN go mod download

# Copy source code dan build
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -mod=mod -o main .

# Stage 2: Final Image (Sangat Ringan)
FROM alpine:latest
RUN apk add --no-cache tzdata
WORKDIR /root/

# Copy binary dari stage builder
COPY --from=builder /app/main .

EXPOSE 3000
CMD ["./main"]