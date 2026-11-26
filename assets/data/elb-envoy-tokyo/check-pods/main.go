package main

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"sync"
	"time"
)

type result struct {
	Target       string         `json:"target"`
	Phase        string         `json:"phase"`
	Sent         int            `json:"sent"`
	Milliseconds int64          `json:"elapsed_ms"`
	Status       map[string]int `json:"responses"`
}

func burst(client *http.Client, target, phase string, count int) result {
	out := result{Target: target, Phase: phase, Sent: count, Status: make(map[string]int)}
	jobs := make(chan int, count)
	for i := 0; i < count; i++ {
		jobs <- i
	}
	close(jobs)
	var workers sync.WaitGroup
	var lock sync.Mutex
	started := time.Now()
	for i := 0; i < 12; i++ {
		workers.Add(1)
		go func() {
			defer workers.Done()
			for j := range jobs {
				response, err := client.Get(fmt.Sprintf("http://%s:8080/capture/%s/%d", target, phase, j))
				key := "error"
				if err == nil {
					var reply struct {
						Instance string `json:"instance"`
					}
					body, readErr := io.ReadAll(response.Body)
					response.Body.Close()
					switch {
					case readErr == nil && response.StatusCode == 200 && json.Unmarshal(body, &reply) == nil && reply.Instance != "":
						key = "200 " + reply.Instance
					case response.StatusCode == 429 && response.Header.Get("x-order-pod-limit") == "true":
						key = "429 x-order-pod-limit=true"
					default:
						key = fmt.Sprintf("unexpected %d", response.StatusCode)
					}
				}
				lock.Lock()
				out.Status[key]++
				lock.Unlock()
			}
		}()
	}
	workers.Wait()
	out.Milliseconds = time.Since(started).Milliseconds()
	return out
}

func main() {
	if len(os.Args) != 3 {
		fmt.Fprintln(os.Stderr, "usage: burst POD_A_IP POD_B_IP")
		os.Exit(2)
	}
	client := &http.Client{Transport: &http.Transport{MaxIdleConnsPerHost: 16}, Timeout: 3 * time.Second}
	out := json.NewEncoder(os.Stdout)
	// Each target gets its own sequential burst. A fresh idle period separates the rounds.
	for _, phase := range []string{"first", "refill"} {
		time.Sleep(2100 * time.Millisecond)
		for _, target := range os.Args[1:] {
			out.Encode(burst(client, target, phase, 140))
		}
	}
}
