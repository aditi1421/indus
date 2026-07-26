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
	client   *whatsmeow.Client
	agentURL string
	groupJID types.JID
	trigger  string
	sendMu   sync.Mutex // serialize + pace outbound sends
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
// must start with prefix (case-insensitive); the prefix is stripped and the
// remainder trimmed. Returns (text, ok) - ok is false when the message should
// be ignored.
func matchTrigger(text, prefix string) (string, bool) {
	text = strings.TrimSpace(text)
	if text == "" {
		return "", false
	}
	if prefix == "" {
		return text, true
	}
	if !strings.HasPrefix(strings.ToLower(text), strings.ToLower(prefix)) {
		return "", false
	}
	return strings.TrimSpace(text[len(prefix):]), true
}

// senderName picks the display name to attribute a message to: pushname if
// present, otherwise the phone number portion of the sender JID.
func senderName(pushName, senderUser string) string {
	if pushName != "" {
		return pushName
	}
	return senderUser
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
	resp, err := http.Post(agentURL+"/chat", "application/json", bytes.NewReader(body))
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

func main() {
	listGroups := len(os.Args) > 1 && os.Args[1] == "-listgroups"
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
	go http.ListenAndServe(sendAddr, nil)
	fmt.Println("gateway up; send API on", sendAddr)

	c := make(chan os.Signal, 1)
	signal.Notify(c, os.Interrupt, syscall.SIGTERM)
	<-c
	client.Disconnect()
}
