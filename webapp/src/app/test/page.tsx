import { getHighScores } from "@/src/lib/supabase/getTeacher";
import { Suspense } from "react";

async function createList() {
  const rows = await getHighScores();
  return (
    <ul>
      {rows?.map((row) => (
        <li key={row["id"]}>{row["name"] + row["rating"]}</li>
      ))}
    </ul>
  );
}

export default function Test() {
  return (
    <div>
      <h1>Teachers</h1>
      <Suspense fallback={<div>Loading...</div>}>
        {createList()}
      </Suspense>
    </div>
  )
}