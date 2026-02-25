const express = require("express");
const client = require("prom-client");
const multer = require("multer");

const app = express();
const upload = multer(); 
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

let focus = "performance";

const focusMetric = new client.Gauge({
    name: "rl_focus",
    help: "RL focus mode",
    labelNames: ["mode"]
});

// Counter untuk trigger perubahan fokus via Button Panel
const focusTrigger = new client.Counter({
    name: "rl_focus_trigger",
    help: "Trigger to change RL focus",
    labelNames: ["mode"]
});

function updateMetric() {
    focusMetric.reset();
    focusMetric.set({ mode: focus }, 1);
}

updateMetric();

app.post("/focus", upload.none(), (req, res) => {
    const f = req.body.focus;
    if (f !== "energy" && f !== "performance") {
        return res.status(400).send("invalid focus");
    }
    focus = f;
    updateMetric();
    console.log("RL focus changed to:", focus);
    res.json({ focus });
});

app.get("/metrics", async (req, res) => {
    res.set("Content-Type", client.register.contentType);
    res.end(await client.register.metrics());
});

app.listen(9105, () => {
    console.log("Focus exporter running on :9105");
});
