const express = require('express');
const axios = require('axios');
const app = express();
const PORT = process.env.PORT || 3000;

// Grab the internal ACA backend target URL from environment variables
const BACKEND_INTERNAL_URL = process.env.BACKEND_API_URL;

app.use(express.json());

// Public Health Check Endpoint
app.get('/health', (req, res) => {
    res.status(200).json({ status: "Public Gateway Online" });
});

// Proxy Gateway Route: Forwards incoming traffic to the internal container app securely
app.all('/api/*', async (req, res) => {
    if (!BACKEND_INTERNAL_URL) {
        return res.status(500).json({ error: "Backend internal route target is unconfigured." });
    }

    try {
        // Reconstruct the internal target endpoint path
        const targetUrl = `${BACKEND_INTERNAL_URL}${req.originalUrl}`;
        
        console.log(`Routing public request internally to: ${targetUrl}`);

        const response = await axios({
            method: req.method,
            url: targetUrl,
            data: req.body,
            headers: {
                ...req.headers,
                host: new URL(BACKEND_INTERNAL_URL).host // Crucial for ACA internal routing verification
            },
            validateStatus: () => true // Pass through all status codes cleanly
        });

        res.status(response.status).send(response.data);
    } catch (error) {
        console.error(`Internal Routing Fault: ${error.message}`);
        res.status(502).json({ error: "Bad Gateway. Unable to communicate with internal microservices." });
    }
});

app.listen(PORT, () => {
    console.log(`Public Application Gateway executing out of port ${PORT}`);
    console.log(`Targeting Internal Backend Cluster at: ${BACKEND_INTERNAL_URL}`);
});