import { useEffect, useState } from "react";
import { ContextSize, ModelDetail } from "../lib/types";
import { eventScope } from "../lib/events";

/** Ollama status, models, the selected model and live system metrics. */
export function useMachine(connected: boolean, inspector: boolean) {
  const [modelInfo, setModelInfo] = useState<Record<string, string | number>>(
    {}
  );
  const [system, setSystem] = useState<Record<string, any>>({});
  const [ollamaOk, setOllamaOk] = useState(false);
  const [models, setModels] = useState<string[]>([]);
  const [modelCount, setModelCount] = useState(0);
  const [modelDetails, setModelDetails] = useState<ModelDetail[]>([]);
  const [spinner, setSpinner] = useState(true);
  // Context size in use for the selected model (AD-028).
  const [context, setContext] = useState<ContextSize | null>(null);

  // Keep system metrics live (the sidecar only pushes once at startup):
  // every 3 s while the Inspector is open, every 10 s otherwise for the
  // sidebar's machine card.
  useEffect(() => {
    if (!connected) return;
    window.nova.call("system");
    const id = window.setInterval(() => {
      window.nova.call("system");
    }, inspector ? 3000 : 10000);
    return () => window.clearInterval(id);
  }, [inspector, connected]);

  useEffect(() => {
    const { on, off } = eventScope();

    on("system", (data) => setSystem(data));
    on("context_size", (data) => setContext(data));
    on("model_list", (data) => {
      setModels(data);
      setModelCount(data.length);
    });
    on("model_details", (data) => {
      setModelDetails(data || []);
    });
    on("status", (data) => {
      setOllamaOk(data.ok);
      setSpinner(false);
    });
    on("model_info", (data) => {
      const p: Record<string, string | number> = {};
      for (const k of [
        "family",
        "parameter_size",
        "quantization",
        "context_length",
      ])
        p[k] = data.details?.[k] ?? "";
      const name: string = data.model || "";
      if (name) {
        p.name = name;
        p.id = name;
      }
      if (data.details?.families) p.family = data.details.families[0] ?? "";
      if (data.context_length) p.context_length = data.context_length;
      setModelInfo(p);
    });

    return off;
  }, []);

  const selectModel = (name: string) => {
    if (!name || name === modelInfo.id) return;
    window.nova.call("selectModel", { name });
  };

  return {
    modelInfo,
    system,
    ollamaOk,
    models,
    modelCount,
    modelDetails,
    spinner,
    setSpinner,
    selectModel,
    context,
  };
}
