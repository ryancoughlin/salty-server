const express = require("express");
const bodyParser = require("body-parser");
const cors = require("cors");
const MongoClient = require("mongodb").MongoClient;
const app = express();
require("dotenv").config();

const defaultRoutes = require("./routes")();

const uri =
  "mongodb+srv://ryancoughlin:LP17i48lB2c7P1FK@cluster0.2qnfz.mongodb.net/salty_prod?w=majority";
const client = new MongoClient(uri, {
  useNewUrlParser: true,
  useUnifiedTopology: true,
});
client.connect((err) => {
  const collection = client.db("salty_prod").collection("stations");
  // perform actions on the collection object
  client.close();
});

app.use(bodyParser.urlencoded({ extended: false }));
app.use(bodyParser.json());

const allowedOrigins = ["http://localhost:5000"];
app.use(
  cors({
    origin: function (origin, callback) {
      if (!origin) return callback(null, true);
      if (allowedOrigins.indexOf(origin) === -1) {
        const msg =
          "The CORS policy for this site does not allow access from the specified Origin.";
        return callback(new Error(msg), false);
      }
      return callback(null, true);
    },
    credentials: true,
  })
);

app.use("/api", defaultRoutes);
app.get("/", (req, res) => res.send("Hello World!"));

app.listen(process.env.PORT, () => console.log("Server is running"));
