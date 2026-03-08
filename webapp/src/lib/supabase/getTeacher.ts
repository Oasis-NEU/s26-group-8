import { createClient } from "@/src/lib/supabase/server";

// Example function for calling a Postgres function (RPC) from Supabase
export async function getHighScores() {
  const supabase = await createClient();
  const { data, error } = await supabase.rpc("get_high_scores", {"amount":10});

  if (error) {
    console.error("Error fetching high scores:", error);
    return null;
  }

  console.log(data);
  return data;
}

//example using the js api to query a table
export async function getTeachers() {
  const supabase = await createClient()
  const { data, error } = await supabase
    .from("RMP (revised 2)")
    .select("*")
    .limit(10);

  if (error) {
    console.error("Error fetching teachers:", error);
    return null;
  }

  return data;
}