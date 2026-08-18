import { Link } from "react-router-dom";

export default function NotFound() {
  return (
    <div>
      <h1>Page not found</h1>
      <p>
        That URL doesn&rsquo;t match anything in Continuum. It may be an old
        bookmark, or a link to a novel that has since been deleted.
      </p>
      <p>
        <Link to="/novels">Back to novels</Link>
      </p>
    </div>
  );
}
