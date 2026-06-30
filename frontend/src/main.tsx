import ReactDOM from "react-dom/client";
import App from "./App";
import "./styles.css";

// No StrictMode: the renderer (CadViewer) and Monaco are imperative singletons;
// the double mount/unmount StrictMode performs in dev corrupts their lifecycle.
ReactDOM.createRoot(document.getElementById("root")!).render(<App />);
