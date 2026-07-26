package main

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"os/signal"
	"strings"
	"sync"
	"syscall"
	"time"

	_ "github.com/mattn/go-sqlite3"
	"github.com/mdp/qrterminal/v3"
	"go.mau.fi/whatsmeow"
	"go.mau.fi/whatsmeow/proto/waE2E"
	"go.mau.fi/whatsmeow/store/sqlstore"
	"go.mau.fi/whatsmeow/types"
	"go.mau.fi/whatsmeow/types/events"
	waLog "go.mau.fi/whatsmeow/util/log"
	"google.golang.org/protobuf/proto"
)

var (
	client      *whatsmeow.Client
	agentURL    string
	groupJID    types.JID
	trigger     string
	sendMu      sync.Mutex // serialize + pace outbound sends
	agentClient = &http.Client{Timeout: 180 * time.Second}
)

func env(k, def string) string {
	if v := os.Getenv(k); v != "" {
		return v
	}
	return def
}

func textOf(msg *waE2E.Message) string {
	if t := msg.GetConversation(); t != "" {
		return t
	}
	return msg.GetExtendedTextMessage().GetText()
}

// matchTrigger applies the TRIGGER_PREFIX filter to an incoming message body.
// If prefix is empty, every message matches unchanged. Otherwise the message
// must start with prefix (case-insensitive) followed by end-of-string or
// whitespace; the prefix is stripped and the remainder trimmed. Returns (text, ok)
// - ok is false when the message should be ignored.
func matchTrigger(text, prefix string) (string, bool) {
	text = strings.TrimSpace(text)
	if text == "" {
		return "", false
	}
	if prefix == "" {
		return text, true
	}
	lower := strings.ToLower(text)
	lowerPrefix := strings.ToLower(prefix)
	if !strings.HasPrefix(lower, lowerPrefix) {
		return "", false
	}
	// Check word boundary: prefix must be followed by end-of-string or whitespace
	afterPrefix := text[len(prefix):]
	if len(afterPrefix) > 0 {
		// First character after prefix must be whitespace
		if !strings.ContainsRune(" \t\n\r", rune(afterPrefix[0])) {
			return "", false
		}
	}
	return strings.TrimSpace(afterPrefix), true
}

// senderName picks the display name to attribute a message to: pushname if
// present, otherwise the phone number portion of the sender JID.
func senderName(pushName, senderUser string) string {
	if pushName != "" {
		return pushName
	}
	return senderUser
}

// normalizePhone strips '+' and whitespace from a phone number and validates
// that only digits remain. Returns an error on empty input or any non-digit
// character (other than the stripped '+'/whitespace).
func normalizePhone(s string) (string, error) {
	var b strings.Builder
	for _, r := range s {
		switch {
		case r == '+' || r == ' ' || r == '\t' || r == '\n' || r == '\r' || r == '-':
			continue
		case r >= '0' && r <= '9':
			b.WriteRune(r)
		default:
			return "", fmt.Errorf("invalid phone number %q: unexpected character %q", s, r)
		}
	}
	out := b.String()
	if out == "" {
		return "", fmt.Errorf("invalid phone number %q: no digits found", s)
	}
	return out, nil
}

func sendText(text string) error {
	sendMu.Lock()
	defer sendMu.Unlock()
	_, err := client.SendMessage(context.Background(), groupJID,
		&waE2E.Message{Conversation: proto.String(text)})
	time.Sleep(2 * time.Second) // pace outbound traffic (ban-risk mitigation)
	return err
}

func askAgent(sender, text string) string {
	body, _ := json.Marshal(map[string]string{
		"chat": groupJID.String(), "sender": sender, "text": text})
	resp, err := agentClient.Post(agentURL+"/chat", "application/json", bytes.NewReader(body))
	if err != nil {
		return "The clerk service is unreachable right now."
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		return ""
	}
	var out struct{ Reply string }
	if json.NewDecoder(resp.Body).Decode(&out) != nil {
		return ""
	}
	return out.Reply
}

func onEvent(evt interface{}) {
	switch v := evt.(type) {
	case *events.Message:
		if v.Info.IsFromMe || v.Info.Chat != groupJID {
			return
		}
		text, ok := matchTrigger(textOf(v.Message), trigger)
		if !ok {
			return
		}
		sender := senderName(v.Info.PushName, v.Info.Sender.User)
		go func() {
			if reply := askAgent(sender, text); reply != "" {
				if err := sendText(reply); err != nil {
					fmt.Println("send error:", err)
				}
			}
		}()
	}
}

// newSendHandler builds the /send HTTP handler, with the actual send action
// injected as a function value so it can be tested without a live WhatsApp
// connection.
func newSendHandler(doSend func(string) error) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		var p struct{ Text string }
		if json.NewDecoder(r.Body).Decode(&p) != nil || p.Text == "" {
			http.Error(w, "bad body", http.StatusBadRequest)
			return
		}
		if err := doSend(p.Text); err != nil {
			fmt.Println("send error:", err)
		}
		w.Write([]byte(`{"ok":true}`))
	}
}

// runPairCode implements `-paircode <phone>`: it generates a WhatsApp
// linking code (no QR scan needed) so the session can be paired remotely,
// then waits for the phone to complete the pairing.
func runPairCode(ctx context.Context, phoneArg string) {
	phone, err := normalizePhone(phoneArg)
	if err != nil {
		fmt.Println("FATAL:", err)
		os.Exit(1)
	}

	if client.Store.ID != nil {
		fmt.Printf("already linked as %s; log out from the phone's Linked Devices first to re-pair\n", client.Store.ID)
		os.Exit(0)
	}

	qrChan, err := client.GetQRChannel(ctx)
	if err != nil {
		fmt.Println("FATAL: could not get QR channel:", err)
		os.Exit(1)
	}
	if err := client.Connect(); err != nil {
		fmt.Println("FATAL: connect failed:", err)
		os.Exit(1)
	}

	// Re-forward the QR channel onto a plain channel so we can select on it
	// alongside a timeout.
	items := make(chan whatsmeow.QRChannelItem)
	go func() {
		for evt := range qrChan {
			items <- evt
		}
		close(items)
	}()

	deadline := time.After(3 * time.Minute)
	codeRequested := false
	for {
		select {
		case evt, ok := <-items:
			if !ok {
				fmt.Println("FATAL: pairing channel closed before completion")
				os.Exit(1)
			}
			switch evt.Event {
			case "code":
				if codeRequested {
					continue
				}
				codeRequested = true
				code, err := client.PairPhone(ctx, phone, true, whatsmeow.PairClientChrome, "Chrome (macOS)")
				if err != nil {
					fmt.Println("FATAL: PairPhone failed:", err)
					os.Exit(1)
				}
				fmt.Printf("Linking code: %s\n", code)
				fmt.Println("On the phone: WhatsApp > Settings > Linked Devices > Link a device > Link with phone number instead, then enter the code.")
			case "success":
				fmt.Printf("LINKED as %s\n", client.Store.ID)
				os.Exit(0)
			default:
				fmt.Println("pairing status:", evt.Event)
				if evt.Error != nil {
					fmt.Println("FATAL:", evt.Error)
					os.Exit(1)
				}
			}
		case <-deadline:
			fmt.Println("FATAL: timed out waiting for pairing to complete (3 minutes)")
			os.Exit(1)
		}
	}
}

func main() {
	listGroups := len(os.Args) > 1 && os.Args[1] == "-listgroups"
	pairCode := len(os.Args) > 1 && os.Args[1] == "-paircode"
	agentURL = env("AGENT_URL", "http://127.0.0.1:8600")
	trigger = env("TRIGGER_PREFIX", "")

	ctx := context.Background()
	container, err := sqlstore.New(ctx, "sqlite3", "file:session.db?_foreign_keys=on", waLog.Noop)
	if err != nil {
		panic(err)
	}
	device, err := container.GetFirstDevice(ctx)
	if err != nil {
		panic(err)
	}
	client = whatsmeow.NewClient(device, waLog.Stdout("WA", "INFO", true))

	if pairCode {
		if len(os.Args) < 3 {
			fmt.Println("FATAL: usage: ./gateway -paircode <phone>")
			os.Exit(1)
		}
		runPairCode(ctx, os.Args[2])
		return
	}

	if !listGroups {
		g := env("GROUP_JID", "")
		if g == "" {
			fmt.Println("FATAL: GROUP_JID env is empty (run ./gateway -listgroups to find it)")
			os.Exit(1)
		}
		groupJID, err = types.ParseJID(g)
		if err != nil {
			panic(err)
		}
		client.AddEventHandler(onEvent)
	}

	if client.Store.ID == nil {
		qrChan, _ := client.GetQRChannel(ctx)
		if err := client.Connect(); err != nil {
			panic(err)
		}
		for evt := range qrChan {
			if evt.Event == "code" {
				qrterminal.GenerateHalfBlock(evt.Code, qrterminal.L, os.Stdout)
				fmt.Println("Scan with the firm's WhatsApp: Settings > Linked Devices")
			} else {
				fmt.Println("QR channel:", evt.Event)
			}
		}
	} else if err := client.Connect(); err != nil {
		panic(err)
	}

	if listGroups {
		time.Sleep(3 * time.Second) // let the connection settle
		groups, err := client.GetJoinedGroups(ctx)
		if err != nil {
			panic(err)
		}
		for _, g := range groups {
			fmt.Printf("%s  |  %s\n", g.JID, g.Name)
		}
		client.Disconnect()
		return
	}

	http.HandleFunc("/send", newSendHandler(sendText))
	sendAddr := env("SEND_ADDR", "127.0.0.1:8601")
	go func() {
		if err := http.ListenAndServe(sendAddr, nil); err != nil {
			fmt.Fprintf(os.Stderr, "send API failed: %v\n", err)
			os.Exit(1)
		}
	}()
	fmt.Println("gateway up; send API on", sendAddr)

	c := make(chan os.Signal, 1)
	signal.Notify(c, os.Interrupt, syscall.SIGTERM)
	<-c
	client.Disconnect()
}
