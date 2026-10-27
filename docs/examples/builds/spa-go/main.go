package main

import (
	"encoding/json"
	"log"
	"net"
	"net/http"
	"os"
	"time"
)

func main() {
	host, port := os.Getenv("HOST"), os.Getenv("PORT")
	if host == "" {
		host = "127.0.0.1"
	}
	if port == "" {
		port = "8081"
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/plain; charset=utf-8")
		_, _ = w.Write([]byte("ok\n"))
	})
	mux.HandleFunc("/api/", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		if r.URL.Path == "/api/error" {
			w.WriteHeader(http.StatusTeapot)
		}
		_ = json.NewEncoder(w).Encode(map[string]string{
			"revision": "v1",
			"path":     r.URL.Path,
			"query":    r.URL.RawQuery,
		})
	})
	server := &http.Server{
		Addr:              net.JoinHostPort(host, port),
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
	}
	log.Fatal(server.ListenAndServe())
}
